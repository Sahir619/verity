"""RunManager.parse: maintenance, daily budget and spend recording (no real provider, no money)."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from verity.contracts import ParseOutOfRange
from verity.core.errors import AdmissionDenied
from verity.core.llm_types import LLMResponse
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings


class Priced:
    name = "priced"

    async def complete(self, req):  # noqa: ANN001, ANN201
        text = json.dumps({"result": "out_of_range", "reason": "r", "closest": None})
        return LLMResponse(text=text, input_tokens=2000, output_tokens=400, model=req.model, request_id="x", latency_ms=1)


def paid_settings(tmp_path, *, key: bool = True):
    """Settings that behave like a paid provider (so spend is recorded)."""
    base = make_settings(tmp_path / "data", provider="nebius")
    secret = SecretStr("test-key") if key else None
    return base.model_copy(update={"llm": base.llm.model_copy(update={"api_key": secret})})


async def make(settings, packs):
    store = SqliteStore(settings)
    await store.open()
    manager = RunManager(settings, store, Priced(), FakeToolRunner(), packs)
    await manager.start()
    return manager, store


async def test_parse_cost_lands_in_the_daily_spend(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    manager, store = await make(settings, packs)
    try:
        reply = await manager.parse("an RTX 6090")
        assert isinstance(reply, ParseOutOfRange)
        spent = await store.daily_spend(datetime.now(UTC).strftime("%Y-%m-%d"))
        price = settings.price_for(settings.model_for("parser"))
        assert spent == pytest.approx((2000 * price.input_per_m + 400 * price.output_per_m) / 1e6)
        assert spent > 0
    finally:
        await manager.shutdown()
        await store.close()


async def test_parse_refused_when_the_day_is_used_up(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    manager, store = await make(settings, packs)
    try:
        await store.add_daily_spend(datetime.now(UTC).strftime("%Y-%m-%d"), settings.limits.daily_usd)
        with pytest.raises(AdmissionDenied) as err:
            await manager.parse("adder")
        assert err.value.reason == "budget_exhausted"
    finally:
        await manager.shutdown()
        await store.close()


async def test_parse_refused_in_maintenance(tmp_path, packs) -> None:
    manager, store = await make(paid_settings(tmp_path, key=False), packs)
    try:
        with pytest.raises(AdmissionDenied) as err:
            await manager.parse("adder")
        assert err.value.reason == "maintenance"
    finally:
        await manager.shutdown()
        await store.close()
