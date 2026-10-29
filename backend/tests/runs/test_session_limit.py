"""The per-session daily spend limit ([limits] session_usd) next to the global daily one: reserve, settle, refuse, a new
UTC day, and a restart. Same pattern as the daily ledger (RunManager._reserve / _spend_view). No network, no money."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from verity.api.errors import _ADMISSION_TEXT
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


def paid_settings(tmp_path, **limits):
    """Settings that behave like a paid provider (so spend is reserved and recorded); the provider object is fake."""
    base = make_settings(tmp_path / "data", provider="nebius")
    llm = base.llm.model_copy(update={"api_key": SecretStr("test-key")})
    return base.model_copy(update={"llm": llm, "limits": base.limits.model_copy(update=limits)})


async def make(settings, packs):
    store = SqliteStore(settings)
    await store.open()
    manager = RunManager(settings, store, Priced(), FakeToolRunner(), packs)
    await manager.start()
    return manager, store


async def close(manager, store) -> None:
    await manager.shutdown()
    await store.close()


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def test_session_spend_is_added_with_the_day_in_one_call(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        await store.add_daily_spend("2026-10-05", 0.25, "s1")
        await store.add_daily_spend("2026-10-05", 0.5, "s1")
        await store.add_daily_spend("2026-10-05", 1.0, "s2")
        await store.add_daily_spend("2026-10-05", 0.1)  # no session: only the day
        await store.add_daily_spend("2026-10-06", 0.3, "s1")
        assert await store.daily_spend("2026-10-05") == pytest.approx(1.85)
        assert await store.session_spend("s1", "2026-10-05") == pytest.approx(0.75)
        assert await store.session_spend("s2", "2026-10-05") == pytest.approx(1.0)
        assert await store.session_spend("s1", "2026-10-06") == pytest.approx(0.3)
        assert await store.session_spend("nobody", "2026-10-05") == 0.0
        with pytest.raises(ValueError):
            await store.add_daily_spend("yesterday", 1.0, "s1")
    finally:
        await store.close()


async def test_the_message_for_the_new_reason_has_no_dollar_figure() -> None:
    text = _ADMISSION_TEXT["session_budget_exhausted"]
    assert text == "You have reached today's spend limit for this session. Try again tomorrow."
    assert "$" not in text and not any(ch.isdigit() for ch in text)


async def test_settle_a_parse_is_charged_to_its_session_and_to_the_day(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    manager, store = await make(settings, packs)
    try:
        await manager.parse("an RTX 6090", session_id="s1")
        price = settings.price_for(settings.model_for("parser"))
        expected = (2000 * price.input_per_m + 400 * price.output_per_m) / 1e6
        assert expected > 0
        assert await store.session_spend("s1", today()) == pytest.approx(expected)
        assert await store.daily_spend(today()) == pytest.approx(expected)
        assert await store.session_spend("s2", today()) == 0.0
        assert manager._parse_holds == {}  # the hold was released
    finally:
        await close(manager, store)


async def test_refuse_when_this_session_has_used_its_day_but_others_still_can(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0, daily_usd=100.0)
    manager, store = await make(settings, packs)
    try:
        await store.add_daily_spend(today(), 1.0, "s1")  # s1 is at its limit
        with pytest.raises(AdmissionDenied) as err:
            await manager.parse("adder", session_id="s1")
        assert err.value.reason == "session_budget_exhausted"
        await manager.parse("adder", session_id="s2")  # another session is not affected
        # The global day still answers first when it is the one that is used up.
        await store.add_daily_spend(today(), 100.0)
        with pytest.raises(AdmissionDenied) as day:
            await manager.parse("adder", session_id="s2")
        assert day.value.reason == "budget_exhausted"
    finally:
        await close(manager, store)


async def test_reserve_holds_count_against_the_session_until_released(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0, daily_usd=100.0)
    manager, store = await make(settings, packs)
    try:
        async with manager._spend_hold(0.7, "s1"):
            with pytest.raises(AdmissionDenied) as err:  # 0.7 held + 0.5 > 1.0
                async with manager._spend_hold(0.5, "s1"):
                    pytest.fail("the second hold must be refused")
            assert err.value.reason == "session_budget_exhausted"
            async with manager._spend_hold(0.5, "s2"):  # another session has its own room
                assert sorted(manager._parse_holds.values()) == [("s1", 0.7), ("s2", 0.5)]
        assert manager._parse_holds == {}
        async with manager._spend_hold(0.5, "s1"):  # released: the room is back
            pass
        # A failed hold leaves nothing behind.
        assert manager._parse_holds == {}
    finally:
        await close(manager, store)


async def test_spend_plus_a_hold_is_checked_together(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0, daily_usd=100.0)
    manager, store = await make(settings, packs)
    try:
        await store.add_daily_spend(today(), 0.6, "s1")
        with pytest.raises(AdmissionDenied) as err:
            async with manager._spend_hold(0.5, "s1"):  # 0.6 spent + 0.5 > 1.0
                pytest.fail("must be refused")
        assert err.value.reason == "session_budget_exhausted"
        async with manager._spend_hold(0.4, "s1"):  # 0.6 + 0.4 fits exactly
            pass
    finally:
        await close(manager, store)


async def test_a_new_utc_day_starts_the_session_again(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0, daily_usd=100.0)
    manager, store = await make(settings, packs)
    try:
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        await store.add_daily_spend(yesterday, 5.0, "s1")  # far over the limit, but on another day
        assert await store.session_spend("s1", today()) == 0.0
        await manager.parse("adder", session_id="s1")  # admitted today
        await store.add_daily_spend(today(), 1.0, "s1")
        with pytest.raises(AdmissionDenied) as err:
            await manager.parse("adder", session_id="s1")
        assert err.value.reason == "session_budget_exhausted"
    finally:
        await close(manager, store)


async def test_the_session_limit_survives_a_restart(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0, daily_usd=100.0)
    manager, store = await make(settings, packs)
    await store.add_daily_spend(today(), 1.0, "s1")
    await close(manager, store)

    manager, store = await make(settings, packs)  # a new process on the same data folder
    try:
        assert await store.session_spend("s1", today()) == pytest.approx(1.0)
        with pytest.raises(AdmissionDenied) as err:
            await manager.parse("adder", session_id="s1")
        assert err.value.reason == "session_budget_exhausted"
    finally:
        await close(manager, store)


async def test_create_run_is_refused_when_the_session_limit_is_used_up(tmp_path, packs) -> None:
    base = make_settings(tmp_path / "data")
    spent = base.model_copy(update={"limits": base.limits.model_copy(update={"session_usd": 0.0})})
    from verity.llm.fake import FakeProvider
    from verity_test_support import tmp_cassettes

    store = SqliteStore(spent)
    await store.open()
    manager = RunManager(spent, store, FakeProvider(spent, delay_s=0.0, cassette_dir=tmp_cassettes(spent)), FakeToolRunner(), packs)
    await manager.start()
    try:
        with pytest.raises(AdmissionDenied) as err:
            await manager.create_run(session_id="s1", preset_id="mac4", priority="area", mode="fast")
        assert err.value.reason == "session_budget_exhausted"
        assert (await manager.health()).live_enabled is True  # the session limit never closes the site for everyone
    finally:
        await close(manager, store)


async def test_the_defaults_are_ten_dollars_a_day_and_three_per_session() -> None:
    from verity.config import load_settings

    limits = load_settings(provider="fake").limits
    assert limits.daily_usd == 10.0 and limits.session_usd == 3.0


async def test_a_run_reserves_its_remaining_budget_and_settles_into_the_session(tmp_path, packs) -> None:
    """A paid-looking run (the fake provider's calls are priced by the table): while it goes, its unspent cap is held
    against its own session only; when it ends, what it really spent is in the session total and the day total."""
    import asyncio

    from verity.llm.fake import FakeProvider
    from verity_test_support import tmp_cassettes

    settings = paid_settings(tmp_path, session_usd=3.0, daily_usd=100.0)
    store = SqliteStore(settings)
    await store.open()
    provider = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    manager = RunManager(settings, store, provider, FakeToolRunner(delay_s=0.05), packs)
    await manager.start()
    try:
        run = await manager.create_run(session_id="s1", preset_id="mac4", priority="area", mode="fast")
        mine = await manager._session_spend("s1")
        other = await manager._session_spend("s2")
        assert mine.reserved and 0 < sum(mine.reserved) <= settings.budget.run.max_usd
        assert other.reserved == []  # the hold belongs to the run's own session
        assert (await manager._daily_spend()).reserved  # and counts against the day as before

        await asyncio.wait_for(_drain(manager, run.id), timeout=60)
        row = await manager.get_run(run.id)
        assert row is not None and row.status == "done" and row.spent.usd > 0
        session_total = await store.session_spend("s1", today())
        assert session_total == pytest.approx(await store.daily_spend(today()))  # one transaction, one amount
        assert session_total == pytest.approx(row.spent.usd, abs=0.001)  # credits events carry three decimals
        assert (await manager._session_spend("s1")).reserved == []  # nothing held once it ended
    finally:
        await close(manager, store)


async def _drain(manager, run_id: str) -> list:
    return [e async for e in manager.stream(run_id, 0)]
