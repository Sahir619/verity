"""RunLLM (the per-run gateway), FakeProvider and CassetteProvider. No network: the real providers are not touched."""

import asyncio
import json
from pathlib import Path

import pytest

from verity.config import Settings
from verity.core.budget import BudgetLimits
from verity.core.errors import BudgetExhausted, LLMError
from verity.core.llm_types import LLMRequest, LLMResponse, PlanOut
from verity.core.pack import TaskPack
from verity.llm.cassette import CassetteProvider, cassette_key
from verity.llm.factory import ProviderGuardError, build_provider, check_provider_guard
from verity.llm.fake import FakeProvider
from verity.llm.gateway import RunLLM
from verity.roles.parsing import parse_rtl_answer
from verity.runs.ledger import RunLedger
from verity_test_support import tmp_cassettes


def response(text: str = "ok", model: str = "m", inp: int = 1000, out: int = 500) -> LLMResponse:
    return LLMResponse(text=text, input_tokens=inp, output_tokens=out, model=model, request_id="req", latency_ms=3)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.requests: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        item = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item(req)
        return item

    async def aclose(self) -> None: ...


def make_gateway(settings: Settings, provider: ScriptedProvider, **limits: float) -> tuple[RunLLM, RunLedger]:
    quick = settings.model_copy(update={"llm": settings.llm.model_copy(update={"retries": 2, "backoff_s": 0.0})})
    ledger = RunLedger(BudgetLimits.from_settings(quick, limits), quick)
    return RunLLM(provider, quick, ledger), ledger  # type: ignore[arg-type]


# ---------- gateway ----------
async def test_a_successful_call_is_routed_priced_and_committed(settings: Settings) -> None:
    provider = ScriptedProvider([response()])
    gateway, ledger = make_gateway(settings, provider)
    reply, usd = await gateway.call("planner", system="s", user="u", response_format="json", prompt_version="p@1", tags={"task_id": "mac4"})
    assert reply.text == "ok"
    request = provider.requests[0]
    assert request.model == settings.llm.model_ultra and request.role == "planner" and request.response_format == "json"
    assert request.max_output_tokens == settings.budget.call.plan_max_output
    assert request.tags["task_id"] == "mac4" and request.tags["prompt_version"] == "p@1" and request.tags["call_seq"] == "1"
    expected = (1000 * 1.0 + 500 * 3.0) / 1_000_000
    assert usd == pytest.approx(expected)
    snap = ledger.snapshot()
    assert (snap.llm_calls, snap.input_tokens, snap.output_tokens) == (1, 1000, 500) and snap.usd == pytest.approx(expected)


async def test_the_optimizer_uses_the_cheaper_model(settings: Settings) -> None:
    provider = ScriptedProvider([response()])
    gateway, _ = make_gateway(settings, provider)
    await gateway.call("optimizer", system="s", user="u")
    assert provider.requests[0].model == settings.llm.model_super
    assert provider.requests[0].temperature == settings.temperature_for("optimizer")


async def test_a_call_is_priced_and_recorded_on_the_model_that_served_it(settings: Settings) -> None:
    provider = ScriptedProvider([response(model=settings.llm.model_super)])  # asked Ultra, answered by Super (a fallback)
    gateway, ledger = make_gateway(settings, provider)
    _, usd = await gateway.call("planner", system="s", user="u")
    assert provider.requests[0].model == settings.llm.model_ultra
    assert usd == pytest.approx((1000 * 0.30 + 500 * 0.90) / 1_000_000)
    assert gateway.calls[0]["model"] == settings.llm.model_ultra and gateway.calls[0]["servedModel"] == settings.llm.model_super


async def test_a_retryable_error_is_retried_and_billed_once(settings: Settings) -> None:
    provider = ScriptedProvider([LLMError("429", retryable=True, status=429), response()])
    gateway, ledger = make_gateway(settings, provider)
    await gateway.call("repair", system="s", user="u")
    assert len(provider.requests) == 2 and ledger.snapshot().llm_calls == 1


async def test_retries_are_bounded_and_the_reservation_is_released(settings: Settings) -> None:
    provider = ScriptedProvider([LLMError("503", retryable=True, status=503)])
    gateway, ledger = make_gateway(settings, provider, max_llm_calls=1)
    with pytest.raises(LLMError):
        await gateway.call("repair", system="s", user="u")
    assert len(provider.requests) == 3  # one try plus llm.retries (2)
    assert ledger.snapshot().llm_calls == 0
    provider.results = [response()]
    await gateway.call("repair", system="s", user="u")  # the failed call did not eat the only slot


async def test_a_permanent_error_is_not_retried(settings: Settings) -> None:
    provider = ScriptedProvider([LLMError("401", retryable=False, status=401)])
    gateway, ledger = make_gateway(settings, provider)
    with pytest.raises(LLMError) as err:
        await gateway.call("planner", system="s", user="u")
    assert len(provider.requests) == 1 and err.value.retryable is False and ledger.snapshot().usd == 0


async def test_an_oversized_prompt_never_reaches_the_provider(settings: Settings) -> None:
    provider = ScriptedProvider([response()])
    gateway, _ = make_gateway(settings, provider)
    with pytest.raises(LLMError) as err:
        await gateway.call("rtl_gen", system="s", user="x" * 80_000)
    assert "per-call limit" in str(err.value) and provider.requests == []


async def test_budget_exhaustion_is_raised_before_any_spend(settings: Settings) -> None:
    provider = ScriptedProvider([response()])
    gateway, _ = make_gateway(settings, provider, max_llm_calls=0)
    with pytest.raises(BudgetExhausted) as err:
        await gateway.call("planner", system="s", user="u")
    assert err.value.limit == "max_llm_calls" and provider.requests == []


async def test_cancellation_releases_the_reservation(settings: Settings) -> None:
    async def slow(req: LLMRequest) -> LLMResponse:
        await asyncio.sleep(30)
        return response()

    provider = ScriptedProvider([slow])
    gateway, ledger = make_gateway(settings, provider, max_llm_calls=1)
    task = asyncio.create_task(gateway.call("planner", system="s", user="u"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    provider.results = [response()]
    await gateway.call("planner", system="s", user="u")  # the single slot is free again
    assert ledger.snapshot().llm_calls == 1


# ---------- fake provider ----------
def request(task_id: str | None, role: str = "planner", **tags: str) -> LLMRequest:
    all_tags = dict(tags)
    if task_id:
        all_tags["task_id"] = task_id
    return LLMRequest(role=role, model="m", system="s", user="u", max_output_tokens=100, temperature=0.0, tags=all_tags)  # type: ignore[arg-type]


async def test_fake_planner_returns_a_valid_plan_for_every_pack(settings: Settings, packs: dict[str, TaskPack]) -> None:
    fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    for pack_id, pack in packs.items():
        reply = await fake.complete(request(pack_id, priority="area"))
        plan = PlanOut.model_validate(json.loads(reply.text))
        assert plan.latency_cycles == pack.latency_cycles and plan.blocks and plan.estimated_lines <= pack.max_lines
        assert reply.request_id.startswith("fake-") and reply.input_tokens > 0


async def test_fake_provider_needs_a_task_id_and_a_known_pack(settings: Settings) -> None:
    fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    with pytest.raises(LLMError):
        await fake.complete(request(None))
    with pytest.raises(LLMError):
        await fake.complete(request("../../etc"))
    with pytest.raises(LLMError):
        await fake.complete(request("not_a_pack"))


async def test_fake_rtl_answers_parse_and_exercise_every_path(settings: Settings, mac4: TaskPack) -> None:
    fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    reference = (mac4.reference_rtl() or "").strip("\n")

    buggy = parse_rtl_answer((await fake.complete(request("mac4", "rtl_gen", attempt="1"))).text, "mac4").text
    assert buggy != reference and buggy.count("module mac4") == 1  # one deliberate bug, so the FIX path runs

    repaired = parse_rtl_answer((await fake.complete(request("mac4", "repair", attempt="2", source_sha="a" * 64))).text, "mac4", want_diagnosis=True)
    assert repaired.diagnosis and repaired.text.endswith(reference.split("\n")[-1])

    texts = []
    for strategy in ("SIMPLIFY", "OPTIMIZE", "EXPLORE"):
        reply = await fake.complete(request("mac4", "optimizer", strategy=strategy, parent_sha="b" * 64, call_seq="5"))
        texts.append(parse_rtl_answer(reply.text, "mac4").text)
    assert len(set(texts)) == 3  # distinct text, so none is dropped as a duplicate

    clean = FakeProvider(settings, delay_s=0.0, inject_bug=False, cassette_dir=tmp_cassettes(settings))
    good = parse_rtl_answer((await clean.complete(request("mac4", "rtl_gen", attempt="1"))).text, "mac4").text
    assert good == reference


async def test_fake_planner_applies_steering_from_the_tags(settings: Settings, mac4: TaskPack) -> None:
    fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    steering = json.dumps([{"kind": "pin", "blockId": "mult", "pin": "Minimize area"}, {"kind": "toggle", "blockId": "mult", "variantId": "pipeline", "value": "2-stage"}])
    plan = PlanOut.model_validate(json.loads((await fake.complete(request("mac4", steering=steering))).text))
    mult = next(b for b in plan.blocks if b.id == "mult")
    assert mult.pins == ["Minimize area"] and mult.variants[0].value == "2-stage"


async def test_a_cassette_overrides_the_canned_answer(settings: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "cassettes" / "mac4"
    folder.mkdir(parents=True)
    (folder / "optimizer.EXPLORE.txt").write_text("REASON: from a cassette\n```verilog\nmodule mac4; endmodule\n```\n", encoding="utf-8")
    fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_path / "cassettes")
    reply = await fake.complete(request("mac4", "optimizer", strategy="EXPLORE", call_seq="1"))
    assert "from a cassette" in reply.text
    other = await fake.complete(request("mac4", "optimizer", strategy="SIMPLIFY", call_seq="1"))
    assert "from a cassette" not in other.text


# ---------- record and replay wrapper ----------
async def test_cassette_provider_records_then_replays(tmp_path: Path) -> None:
    inner = ScriptedProvider([response("first answer", "model-x", 12, 34)])
    wrapped = CassetteProvider(inner, tmp_path / "tapes")  # type: ignore[arg-type]
    req = request("mac4", "planner")
    one = await wrapped.complete(req)
    two = await wrapped.complete(req)
    assert len(inner.requests) == 1, "the second identical request is replayed"
    assert one.text == two.text == "first answer" and two.request_id.startswith("cassette:") and two.latency_ms == 0
    assert (two.input_tokens, two.output_tokens, two.model) == (12, 34, "model-x")
    different = LLMRequest(role="planner", model="m", system="s", user="another prompt", max_output_tokens=100, temperature=0.0)
    await wrapped.complete(different)
    assert len(inner.requests) == 2 and cassette_key(req) != cassette_key(different)
    assert wrapped.name == "scripted"


async def test_a_damaged_cassette_is_a_miss_not_a_failure(tmp_path: Path) -> None:
    inner = ScriptedProvider([response("fresh")])
    wrapped = CassetteProvider(inner, tmp_path)  # type: ignore[arg-type]
    req = request("mac4", "planner")
    (tmp_path / f"{cassette_key(req)}.json").write_text("{not json", encoding="utf-8")
    assert (await wrapped.complete(req)).text == "fresh"


# ---------- factory and production guard ----------
def test_production_requires_nebius_and_the_token_factory_host(settings: Settings) -> None:
    prod = settings.model_copy(update={"env": "production", "provider": "fake"})
    with pytest.raises(ProviderGuardError):
        check_provider_guard(prod)
    with pytest.raises(ProviderGuardError):
        build_provider(prod)
    wrong_host = prod.model_copy(update={"provider": "nebius", "llm": prod.llm.model_copy(update={"base_url": "http://localhost:1/v1"})})
    with pytest.raises(ProviderGuardError):
        check_provider_guard(wrong_host)
    check_provider_guard(settings)  # development is never blocked


def test_the_fake_provider_is_built_without_the_openai_package(settings: Settings) -> None:
    provider = build_provider(settings)
    assert provider.name == "fake"
