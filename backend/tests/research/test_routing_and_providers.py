"""Per-role routing and prices (claim 5), the per-model JSON flag, the gateway call_seq race, log redaction."""

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import SecretStr

from verity.api.redact import redact
from verity.config import ALL_ROLES, RouteConfig, load_settings
from verity.core.budget import BudgetLimits
from verity.core.llm_types import LLMRequest, LLMResponse
from verity.llm.gateway import RunLLM
from verity.llm.openai_compat import OpenAICompatProvider
from verity.research import build_search_adapter
from verity.runs.ledger import RunLedger
from verity_test_support import make_settings

GLM = "zai-org/GLM-5.3"
LIGHTNING = "nvidia/Nemotron-3_5-Lightning"


def test_each_role_has_its_own_model_and_nemotron_keeps_the_engineering(tmp_path) -> None:
    s = make_settings(tmp_path)
    models = s.models_by_role()
    assert set(models) == set(ALL_ROLES)
    # Nemotron is the engineer: plan (Ultra), every line of Verilog (Ultra writes and fixes, Super optimizes), router.
    assert models["planner"] == models["rtl_gen"] == models["repair"] == s.llm.model_ultra
    assert models["optimizer"] == s.llm.model_super
    assert models["router"] == LIGHTNING
    # GLM 5.3 writes the chat answers, the fact-check class and the research summaries.
    assert models["parser"] == models["chat"] == models["research"] == models["memory"] == GLM


def test_model_id_wins_over_the_tier_and_old_tiers_stay_the_fallback(tmp_path) -> None:
    s = make_settings(tmp_path)
    routes = dict(s.routes)
    routes["chat"] = RouteConfig(tier="ultra")  # no model id: the tier decides
    routes["planner"] = RouteConfig(tier="ultra", model="some/other-model")
    t = s.model_copy(update={"routes": routes})
    assert t.model_for("chat") == s.llm.model_ultra
    assert t.model_for("planner") == "some/other-model"


def test_new_roles_have_their_own_output_caps_and_timeouts(tmp_path) -> None:
    s = make_settings(tmp_path)
    assert s.max_output_for("planner") == s.budget.call.plan_max_output
    assert s.max_output_for("rtl_gen") == s.budget.call.rtl_max_output
    assert {r: s.max_output_for(r) for r in ("router", "parser", "chat", "research", "memory")} == {
        "router": 300,
        "parser": 2000,
        "chat": 2000,
        "research": 3000,
        "memory": 800,
    }
    assert s.llm_timeout_for("router") == 30 and s.llm_timeout_for("research") == 45 and s.llm_timeout_for("memory") == 25
    assert s.temperature_for("memory") == 0.0 and s.reasoning_effort_for("memory") == "low"
    assert s.llm_timeout_for("planner") == s.timeouts.llm_ultra_s


def test_prices_come_from_the_nebius_catalog(tmp_path) -> None:
    s = make_settings(tmp_path)
    assert (s.price_for(GLM).input_per_m, s.price_for(GLM).output_per_m) == (1.40, 4.40)
    flash = s.price_for("zai-org/GLM-5.3-Flash")
    assert (flash.input_per_m, flash.output_per_m) == (0.15, 0.50)
    lightning = s.price_for(LIGHTNING)
    assert (lightning.input_per_m, lightning.output_per_m) == (0.06, 0.24)
    assert s.price_for("unknown/model") == s.prices["fallback"]


def test_a_config_without_chat_routes_still_gets_them(tmp_path) -> None:
    toml = tmp_path / "old.toml"
    toml.write_text(
        '[routes.planner]\ntier = "ultra"\n[routes.rtl_gen]\ntier = "ultra"\n'
        '[routes.repair]\ntier = "ultra"\n[routes.optimizer]\ntier = "super"\n',
        encoding="utf-8",
    )
    s = load_settings(toml, provider="fake")
    assert s.model_for("parser") == GLM and s.model_for("router") == LIGHTNING


def test_research_adapter_choice_and_the_production_guard(tmp_path) -> None:
    s = make_settings(tmp_path)
    no_key = s.model_copy(
        update={"research": s.research.model_copy(update={"api_key": None, "tavily_api_key": None, "adapter": "auto"})}
    )
    assert no_key.provider == "fake" and no_key.research_adapter() == "fake"
    keyed = no_key.model_copy(update={"research": no_key.research.model_copy(update={"api_key": SecretStr("k")})})
    assert keyed.research_adapter() == "fake"  # the fake LLM provider never spends Serper credits
    assert keyed.model_copy(update={"provider": "nebius"}).research_adapter() == "serper"
    # A real provider without a key: research is off, so no canned source is summarised and shown as a real citation.
    real_no_key = no_key.model_copy(update={"provider": "nebius", "env": "development"})
    assert real_no_key.research_adapter() == "off"
    assert build_search_adapter(real_no_key) is None  # so main.py builds no ResearchService and /health lists no "research"
    assert build_search_adapter(no_key) is not None  # the fake LLM provider keeps its canned sources for demos and tests
    assert no_key.model_copy(update={"env": "production"}).research_adapter() == "off"
    assert real_no_key.model_copy(update={"env": "production"}).research_adapter() == "off"
    prod = keyed.model_copy(update={"env": "production", "provider": "nebius"})
    bad_host = prod.model_copy(
        update={"research": prod.research.model_copy(update={"serper_base_url": "https://evil.example.com"})}
    )
    assert any("serper_base_url host" in p for p in bad_host.production_problems())
    fake_in_prod = prod.model_copy(update={"research": prod.research.model_copy(update={"adapter": "fake"})})
    assert any("not allowed in production" in p for p in fake_in_prod.production_problems())
    # The LLM host guard is unchanged.
    moved = prod.model_copy(update={"llm": prod.llm.model_copy(update={"base_url": "https://elsewhere.example.com/v1"})})
    assert any("LLM_BASE_URL host" in p for p in moved.production_problems())


# ---------- provider: JSON mode per model ----------
class JsonRefusingClient:
    """Refuses response_format json_object for one model only."""

    def __init__(self, refuses: str) -> None:
        self.refuses = refuses
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    async def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(kwargs)
        if kwargs["model"] == self.refuses and "response_format" in kwargs:
            response = httpx.Response(400, request=httpx.Request("POST", "http://test/v1/chat/completions"))
            raise openai.BadRequestError("response_format json_object is not supported", response=response, body=None)
        completion = SimpleNamespace(
            id="c",
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, completion_tokens_details=None),
        )
        return SimpleNamespace(parse=lambda: completion, headers={})


def _req(model: str) -> LLMRequest:
    return LLMRequest(role="parser", model=model, system="s", user="u", max_output_tokens=10, temperature=0.0, response_format="json")


async def test_one_models_json_refusal_does_not_turn_json_off_for_the_others(tmp_path) -> None:
    provider = OpenAICompatProvider(make_settings(tmp_path))
    client = provider._client = JsonRefusingClient(refuses="old/model")  # type: ignore[assignment]
    first = await provider.complete(_req("old/model"))
    assert first.text == "{}" and len(client.calls) == 2  # refused, then asked again without JSON mode
    assert not provider.json_mode_supported("old/model") and provider.json_mode_supported(GLM)
    await provider.complete(_req(GLM))
    assert client.calls[-1]["response_format"] == {"type": "json_object"}
    await provider.complete(_req("old/model"))
    assert "response_format" not in client.calls[-1]


# ---------- gateway: call_seq under parallel calls ----------
class ReversedProvider:
    """The first call finishes last."""

    name = "reversed"

    def __init__(self) -> None:
        self.release_first = asyncio.Event()

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if req.tags.get("call_seq") == "1":
            await self.release_first.wait()
        else:
            self.release_first.set()
        return LLMResponse(text="x", input_tokens=10, output_tokens=10, model=req.model, request_id="r", latency_ms=1)

    async def aclose(self) -> None:
        return None


async def test_parallel_calls_keep_their_own_sequence_number(tmp_path) -> None:
    settings = make_settings(tmp_path)
    ledger = RunLedger(BudgetLimits.from_settings(settings), settings)
    gateway = RunLLM(ReversedProvider(), settings, ledger)
    await asyncio.gather(
        gateway.call("optimizer", system="s", user="u"),
        gateway.call("optimizer", system="s", user="u"),
    )
    assert sorted(c["seq"] for c in gateway.calls) == [1, 2]
    assert [c["seq"] for c in gateway.calls] == [2, 1]  # completion order, each with its own number
    assert all(c["servedModel"] == settings.model_for("optimizer") for c in gateway.calls)


def test_the_serper_key_header_is_redacted() -> None:
    line = "POST https://google.serper.dev/search headers={'X-API-KEY': 'sk-123abc', 'Content-Type': 'json'}"
    out = redact(line)
    assert "sk-123abc" not in out and "[redacted]" in out
    assert "sk-9" not in redact("X-API-KEY: sk-9")


@pytest.mark.parametrize("role", ["router", "parser", "chat", "research", "memory"])
def test_chat_roles_are_known_to_the_role_literal(tmp_path, role) -> None:
    assert role in make_settings(tmp_path).routes
