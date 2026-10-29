"""Model fallback chains (llm.fallback): a primary that times out, cannot be reached or answers 5xx is skipped for the
cooldown and its requests go down the chain. No network: a stub client answers per model id, the clock is a list."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from verity.config import FallbackChain, FallbackStep, load_settings
from verity.core.errors import LLMError
from verity.core.llm_types import LLMRequest
from verity.llm import openai_compat
from verity.llm.openai_compat import OpenAICompatProvider
from verity_test_support import make_settings

GLM = "zai-org/GLM-5.3"
SECOND = "vendor/Second-Model"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
DEEPSEEK = "deepseek-ai/DeepSeek-V4-Pro"
REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def timeout() -> Exception:
    return openai.APITimeoutError(request=REQUEST)


def status(code: int) -> Exception:
    response = httpx.Response(code, request=REQUEST, json={"error": "x"})
    return openai.APIStatusError("endpoint said no", response=response, body=None)


class StubClient:
    """Answers per model id: an Exception is raised, a string is the answer text. Remembers every call in order."""

    def __init__(self, script: dict[str, object]) -> None:
        self.script = script
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]

    async def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(kwargs)
        item = self.script[kwargs["model"]]
        if isinstance(item, Exception):
            raise item
        completion = SimpleNamespace(
            id="c1",
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=str(item)), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, completion_tokens_details=None),
        )
        return SimpleNamespace(parse=lambda: completion, headers={})


def request(model: str = GLM, **extra) -> LLMRequest:
    fields = {
        "role": "parser", "system": "s", "user": "SECRET PROMPT TEXT", "max_output_tokens": 2_000, "temperature": 0.0,
        "response_format": "text", "timeout_s": 25.0, "reasoning_effort": "low",
    }
    return LLMRequest(model=model, **{**fields, **extra})


@pytest.fixture()
def clock(monkeypatch):  # noqa: ANN001, ANN201
    now = [1_000.0]
    monkeypatch.setattr(openai_compat, "_now", lambda: now[0])
    return now


def make(tmp_path, script: dict[str, object], chain: list[FallbackStep] | None = None, cooldown: float = 300.0):  # noqa: ANN001, ANN201
    base = make_settings(tmp_path)
    steps = chain if chain is not None else [FallbackStep(model=ULTRA, thinking=False)]
    llm = base.llm.model_copy(update={"fallback": {GLM: FallbackChain(chain=steps)}, "fallback_cooldown_s": cooldown})
    provider = OpenAICompatProvider(base.model_copy(update={"llm": llm}))
    client = provider._client = StubClient(script)  # type: ignore[assignment]
    return provider, client


# ---------- the fallback itself ----------
async def test_a_timeout_on_the_primary_is_answered_by_the_fallback(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "from ultra"})
    response = await provider.complete(request())
    assert response.text == "from ultra" and response.model == ULTRA
    assert client.models == [GLM, ULTRA]
    primary, fallback = client.calls
    assert primary["extra_body"] == {"reasoning_effort": "low"} and primary["timeout"] == 25.0
    # the fallback's own thinking setting, no reasoning_effort, the same output cap, at least 30 s
    assert fallback["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert fallback["max_tokens"] == 2_000 and fallback["timeout"] == 30.0 and fallback["messages"] == primary["messages"]


async def test_a_longer_primary_timeout_is_kept_for_the_fallback(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "ok"})
    await provider.complete(request(timeout_s=45.0))
    assert [c["timeout"] for c in client.calls] == [45.0, 45.0]


@pytest.mark.parametrize("failure", [timeout(), openai.APIConnectionError(request=REQUEST), status(503), status(500)])
async def test_a_timeout_a_connection_error_or_a_5xx_falls_back(tmp_path, clock, failure) -> None:
    provider, client = make(tmp_path, {GLM: failure, ULTRA: "ok"})
    assert (await provider.complete(request())).model == ULTRA
    assert client.models == [GLM, ULTRA]


async def test_the_primary_is_skipped_during_the_cooldown_and_used_again_after_it(tmp_path, clock, caplog) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "from ultra"})
    with caplog.at_level("WARNING", logger="verity.llm"):
        await provider.complete(request())
        client.script[GLM] = "from glm"  # the endpoint recovers, but the cooldown has not run out
        clock[0] += 299
        assert (await provider.complete(request())).model == ULTRA
        assert client.models == [GLM, ULTRA, ULTRA]  # GLM was not asked again
        clock[0] += 2  # 301 s after the failure
        assert (await provider.complete(request())).model == GLM
        assert client.models[-1] == GLM
        assert (await provider.complete(request())).model == GLM  # healthy again: no more fallback
    lines = [r.getMessage() for r in caplog.records if r.name == "verity.llm"]
    assert len(lines) == 2 and f"{GLM} is unhealthy" in lines[0] and f"{GLM} is healthy again" in lines[1]
    assert not any("SECRET" in line for line in lines)


async def test_a_primary_that_is_still_down_after_the_cooldown_is_marked_again(tmp_path, clock, caplog) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "ok"})
    with caplog.at_level("WARNING", logger="verity.llm"):
        await provider.complete(request())
        clock[0] += 301
        await provider.complete(request())  # tried again, down again
        clock[0] += 10
        await provider.complete(request())  # skipped
    assert client.models == [GLM, ULTRA, GLM, ULTRA, ULTRA]
    assert [r.getMessage().split(" is ")[1][:9] for r in caplog.records] == ["unhealthy", "healthy a", "unhealthy"]


@pytest.mark.parametrize("failure", [status(404), status(422), status(429), status(401)])
async def test_a_4xx_on_the_primary_does_not_fall_back(tmp_path, clock, failure) -> None:
    provider, client = make(tmp_path, {GLM: failure, ULTRA: "ok"})
    with pytest.raises(LLMError) as caught:
        await provider.complete(request())
    assert caught.value.status == failure.status_code
    assert client.models == [GLM]
    with pytest.raises(LLMError):
        await provider.complete(request())
    assert client.models == [GLM, GLM]  # a 4xx does not mark the model unhealthy


async def test_a_model_with_no_fallback_raises_as_before(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {ULTRA: timeout(), GLM: "ok"})
    with pytest.raises(LLMError, match="timed out") as caught:
        await provider.complete(request(model=ULTRA, reasoning_effort=None))
    assert caught.value.retryable and client.models == [ULTRA]
    empty, client2 = make(tmp_path, {GLM: status(503), ULTRA: "ok"}, chain=[])  # an empty chain is no chain
    with pytest.raises(LLMError, match="HTTP 503"):
        await empty.complete(request())
    assert client2.models == [GLM]


async def test_a_bad_answer_does_not_fall_back(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: "ok", ULTRA: "ok"})

    async def no_choices(**kwargs):  # noqa: ANN003, ANN202
        client.calls.append(kwargs)
        completion = SimpleNamespace(id="c", model=GLM, choices=[], usage=None)
        return SimpleNamespace(parse=lambda: completion, headers={})

    client.chat.completions.with_raw_response.create = no_choices
    with pytest.raises(LLMError, match="no choices"):
        await provider.complete(request())
    assert client.models == [GLM]


# ---------- a chain of two ----------
def two(**extra) -> list[FallbackStep]:  # noqa: ANN003
    return [FallbackStep(model=SECOND, reasoning_effort="low", **extra), FallbackStep(model=ULTRA, thinking=False)]


async def test_the_second_fallback_answers_when_the_first_also_times_out(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), SECOND: status(502), ULTRA: "from ultra"}, chain=two())
    response = await provider.complete(request())
    assert response.model == ULTRA and response.text == "from ultra"
    assert client.models == [GLM, SECOND, ULTRA]
    assert client.calls[1]["extra_body"] == {"reasoning_effort": "low"}  # each entry carries its own settings
    assert client.calls[2]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    await provider.complete(request())
    assert client.models[3:] == [ULTRA]  # both are in cooldown: straight to the third


async def test_an_entry_in_its_own_cooldown_is_skipped(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), SECOND: "from second", ULTRA: "from ultra"}, chain=two())
    assert (await provider.complete(request())).model == SECOND  # t=1000: GLM down until 1300
    clock[0] = 1_100
    client.script[SECOND] = timeout()
    assert (await provider.complete(request())).model == ULTRA  # GLM skipped; SECOND down until 1400
    clock[0] = 1_350
    assert (await provider.complete(request())).model == ULTRA  # GLM is tried again and fails again; SECOND is skipped
    assert client.models == [GLM, SECOND, SECOND, ULTRA, GLM, ULTRA]


async def test_with_every_model_down_each_is_tried_and_the_last_error_is_raised(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), SECOND: timeout(), ULTRA: status(503)}, chain=two())
    with pytest.raises(LLMError, match="HTTP 503"):
        await provider.complete(request())
    assert client.models == [GLM, SECOND, ULTRA]
    client.script[ULTRA] = "back"
    response = await provider.complete(request())  # all three cooling down: they are all tried rather than none
    assert response.model == ULTRA and client.models[3:] == [GLM, SECOND, ULTRA]
    await provider.complete(request())
    assert client.models[6:] == [ULTRA]  # Ultra answered, so only the other two are still skipped


# ---------- config ----------
def test_the_shipped_config_falls_back_from_both_glm_models_to_deepseek_then_nemotron_ultra() -> None:
    settings = load_settings(provider="fake")
    for model in (GLM, "zai-org/GLM-5.3-Flash"):
        chain = settings.fallback_chain_for(model)
        assert [(s.model, s.thinking, s.reasoning_effort) for s in chain] == [(DEEPSEEK, None, None), (ULTRA, False, None)]
    assert settings.fallback_chain_for(ULTRA) == [] and settings.llm.fallback_cooldown_s == 300
    deepseek = settings.price_for(DEEPSEEK)  # a third-party listing of 2026-10-23, not on the Nebius page
    assert (deepseek.input_per_m, deepseek.output_per_m) == (1.75, 3.50)
    timeouts = {role: settings.llm_timeout_for(role) for role in ("parser", "chat", "research", "memory")}  # type: ignore[arg-type]
    assert timeouts == {"parser": 25, "chat": 30, "research": 45, "memory": 25}
    assert settings.llm_timeout_for("planner") == 120 and settings.llm_timeout_for("optimizer") == 120  # Nemotron untouched


def test_env_overrides_the_cooldown_and_the_chains(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("VERITY_LLM__FALLBACK_COOLDOWN_S", "60")
    monkeypatch.setenv("VERITY_LLM__FALLBACK", '{"zai-org/GLM-5.3": {"chain": [{"model": "a/b", "reasoning_effort": "low"}]}}')
    settings = load_settings(provider="fake")
    assert settings.llm.fallback_cooldown_s == 60
    assert [(s.model, s.reasoning_effort) for s in settings.fallback_chain_for(GLM)] == [("a/b", "low")]
    monkeypatch.setenv("VERITY_LLM__FALLBACK", '{"zai-org/GLM-5.3": {"chain": []}}')
    assert load_settings(provider="fake").fallback_chain_for(GLM) == []  # an empty chain turns one model's fallback off


def test_a_chain_that_lists_its_own_primary_is_refused(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("VERITY_LLM__FALLBACK", '{"zai-org/GLM-5.3": {"chain": [{"model": "zai-org/GLM-5.3"}]}}')
    with pytest.raises(ValueError, match="lists zai-org/GLM-5.3 itself"):
        load_settings(provider="fake")


# ---------- the hold prices the dearest model of the chain ----------
def test_a_hold_prices_each_call_at_the_highest_input_and_output_price_of_the_primary_and_its_chain() -> None:
    settings = load_settings(provider="fake")
    glm, deepseek, ultra = settings.price_for(GLM), settings.price_for(DEEPSEEK), settings.price_for(ULTRA)
    assert deepseek.input_per_m > glm.input_per_m and deepseek.output_per_m < glm.output_per_m  # neither model is dearer on both
    held = settings.hold_price_for(GLM)
    assert (held.input_per_m, held.output_per_m) == (deepseek.input_per_m, glm.output_per_m) == (1.75, 4.40)
    assert settings.hold_price_for("zai-org/GLM-5.3-Flash").input_per_m == 1.75
    assert settings.hold_price_for(ULTRA) == ultra  # a model with no chain is priced as itself
    assert settings.hold_price_for("vendor/unknown") == settings.prices["fallback"]
    # a chain model dearer on output than the primary raises the output price of the hold
    llm = settings.llm.model_copy(update={"fallback": {GLM: FallbackChain(chain=[FallbackStep(model="vendor/dear")])}})
    dear = settings.model_copy(update={"llm": llm, "prices": {**settings.prices, "vendor/dear": type(glm)(input_per_m=0.1, output_per_m=9.0)}})
    assert (dear.hold_price_for(GLM).input_per_m, dear.hold_price_for(GLM).output_per_m) == (glm.input_per_m, 9.0)


# ---------- a request deadline caps the fallback attempts ----------
def spending(client: StubClient, clock: list[float], seconds: dict[str, float]) -> None:
    """Every call to a model takes this many (fake) seconds of the cooldown clock before it answers or fails."""
    original = client.chat.completions.with_raw_response.create

    async def create(**kwargs):  # noqa: ANN003, ANN202
        clock[0] += seconds.get(kwargs["model"], 0.0)
        return await original(**kwargs)

    client.chat.completions.with_raw_response.create = create


async def test_a_fallback_attempt_gets_at_most_the_time_left_of_the_request_deadline(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "from ultra"})
    spending(client, clock, {GLM: 25.0})
    response = await provider.complete(request(timeout_s=25.0, deadline_s=40.0))
    assert response.model == ULTRA
    assert [c["timeout"] for c in client.calls] == [25.0, 15.0]  # 40 s minus the 25 s the primary took, not the usual 30 s


async def test_a_fallback_attempt_is_skipped_with_under_five_seconds_left(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "from ultra"})
    spending(client, clock, {GLM: 25.0})
    with pytest.raises(LLMError, match="timed out"):
        await provider.complete(request(timeout_s=25.0, deadline_s=29.9))  # 4.9 s left: the primary's own error is raised
    assert client.models == [GLM]
    clock[0] += 400  # the cooldown is over
    response = await provider.complete(request(timeout_s=25.0, deadline_s=30.0))  # exactly 5 s left: one attempt, of 5 s
    assert response.model == ULTRA and [c["timeout"] for c in client.calls][1:] == [25.0, 5.0]


async def test_the_second_fallback_gets_what_the_first_left(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), SECOND: timeout(), ULTRA: "from ultra"}, chain=two())
    spending(client, clock, {GLM: 10.0, SECOND: 20.0})
    response = await provider.complete(request(timeout_s=10.0, deadline_s=45.0))
    assert response.model == ULTRA
    assert [c["timeout"] for c in client.calls] == [10.0, 30.0, 15.0]  # 35 s left for the first, then 15 s for the second


async def test_with_the_primary_cooling_down_and_no_time_left_no_model_is_asked(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "from ultra"})
    await provider.complete(request())  # GLM is down for the cooldown, Ultra answered
    with pytest.raises(LLMError, match="no time left") as caught:
        await provider.complete(request(deadline_s=4.0))
    assert not caught.value.retryable and client.models == [GLM, ULTRA]  # nothing new was asked


async def test_a_request_with_no_deadline_keeps_the_thirty_second_minimum(tmp_path, clock) -> None:
    provider, client = make(tmp_path, {GLM: timeout(), ULTRA: "ok"})
    spending(client, clock, {GLM: 25.0})
    await provider.complete(request())
    assert [c["timeout"] for c in client.calls] == [25.0, 30.0]
