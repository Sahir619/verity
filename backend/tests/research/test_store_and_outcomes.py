"""Migration 3 from a version 2 database and migration 4 (Tavily credits) from a version 3 one, research records and grants, run outcomes from both endings, the lookup order
for a new run, researchId on POST /runs, and what reaches the Nemotron roles."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from verity.api.app import create_app
from verity.core.parse import live_families
from verity.core.records import ResearchRecord, ResearchSourceRow
from verity.llm.fake import FakeProvider
from verity.research import FakeSearchAdapter, ResearchService
from verity.research.outcomes import record_run_outcome
from verity.research.queries import canonical_key
from verity.research.service import RunReferences, knob_distance, session_hmac
from verity.roles.optimizer import optimize
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity.store.migrations import MIGRATIONS
from verity_test_support import FakeToolRunner, make_settings, tmp_cassettes


def now() -> datetime:
    return datetime.now(UTC)


def research(rid: str, family: str, params: dict, *, key: bool = True, techniques=None, status="ok", age_days=0) -> ResearchRecord:
    created = now() - timedelta(days=age_days)
    return ResearchRecord(
        id=rid,
        created_at=created,
        expires_at=created + timedelta(days=30),
        key=canonical_key(family, params) if key else None,
        query_key=f"q-{rid}",
        family=family,
        params=params,
        intent_class="in_family" if key else "near_family",
        intent_label="adder",
        text_sha256="0" * 64,
        verdict="buildable_now",
        status=status,
        queries=[{"endpoint": "search", "q": "16-bit adder architecture"}],
        sentences=[{"text": "Adders are known.", "sourceIds": [1]}],
        techniques=techniques or ["carry_select"],
        done_before=True,
        sources=[
            ResearchSourceRow(n=1, url=f"https://arxiv.org/abs/{rid}", domain="arxiv.org", tier="A", title="Adders", endpoint="scholar")
        ],
    )


# ---------- migration ----------
async def test_a_version_2_database_migrates_to_3_and_keeps_its_runs(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = sqlite3.connect(settings.data_dir / "verity.db")
    db.executescript(MIGRATIONS[0])
    db.executescript(MIGRATIONS[1])
    db.execute(
        "INSERT INTO runs (id, session_id, preset_id, priority, mode, status, created_at, budget_json, spent_json, "
        "task_pack_sha256, params_json, title) VALUES ('r_old', 's', 'add8', 'area', 'full', 'done', ?, ?, '{}', 'abc', NULL, 'old')",
        (now().isoformat(), make_settings(tmp_path / "x").budget.run.model_dump_json()),
    )
    db.execute("INSERT INTO daily_spend (day, usd) VALUES ('2026-10-01', 1.5)")
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()

    store = SqliteStore(settings)
    await store.open()
    try:
        old = await store.get_run("r_old")
        assert old is not None and old.research_id is None and old.title == "old"
        assert await store.daily_spend("2026-10-01") == 1.5 and await store.serper_credits("2026-10-01") == 0
        await store.add_serper_credits("2026-10-01", 3)
        assert await store.serper_credits("2026-10-01") == 3 and await store.daily_spend("2026-10-01") == 1.5
        row = await store._fetchone("PRAGMA user_version")
        assert row is not None and row[0] == len(MIGRATIONS) == 10  # 3 research, 4 Tavily, 5 per-session spend, 6 threads, 7 run list, 8 user_credits, 9 email/role, 10 CPU jobs
        tables = {r[0] for r in await store._fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"research", "source", "research_source", "research_grant", "run_outcome", "strategy_outcome"} <= tables
        columns = {r[1] for r in await store._fetchall("PRAGMA table_info(research)")}
        assert "text_sha256" in columns and not any("text" == c or c.startswith("user_text") for c in columns)
    finally:
        await store.close()
    again = SqliteStore(settings)  # re-opening runs nothing new
    await again.open()
    await again.close()


async def test_a_version_3_database_gets_the_tavily_credit_columns(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = sqlite3.connect(settings.data_dir / "verity.db")
    for script in MIGRATIONS[:3]:
        db.executescript(script)
    db.execute("INSERT INTO daily_spend (day, usd, serper_credits) VALUES ('2026-10-01', 1.5, 7)")
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    db.close()

    store = SqliteStore(settings)
    await store.open()
    try:
        assert await store.tavily_credits("2026-10-01") == 0 and await store.serper_credits("2026-10-01") == 7
        await store.add_tavily_credits("2026-10-01", 2)
        await store.add_tavily_credits("2026-10-02", 5)  # a day with no spend row yet
        assert await store.tavily_credits("2026-10-01") == 2 and await store.tavily_credits("2026-10-02") == 5
        assert await store.serper_credits("2026-10-01") == 7 and await store.daily_spend("2026-10-01") == 1.5
        with pytest.raises(ValueError):
            await store.add_tavily_credits("yesterday", 1)
        await store.put_research(research("rs_t", "add8", {"width": 16}).model_copy(update={"tavily_credits": 2, "serper_credits": 1}))
        got = await store.get_research("rs_t")
        assert got is not None and got.tavily_credits == 2 and got.serper_credits == 1
    finally:
        await store.close()


async def test_research_round_trip_freshness_and_grants(tmp_path) -> None:
    store = SqliteStore(make_settings(tmp_path / "data"))
    await store.open()
    try:
        rec = research("rs_a", "add8", {"width": 16})
        await store.put_research(rec)
        got = await store.get_research("rs_a")
        assert got is not None and got.sources[0].url == "https://arxiv.org/abs/rs_a" and got.techniques == ["carry_select"]
        assert (await store.research_by_key(rec.key, now())).id == "rs_a"  # type: ignore[arg-type, union-attr]
        assert await store.research_by_key(rec.key, now() + timedelta(days=31)) is None  # type: ignore[arg-type]
        await store.put_research(research("rs_bad", "add8", {"width": 20}, status="failed"))
        assert await store.research_by_key(canonical_key("add8", {"width": 20}), now()) is None
        await store.grant_research("rs_a", session_hmac("s1"))
        assert await store.research_granted("rs_a", session_hmac("s1"))
        assert not await store.research_granted("rs_a", session_hmac("s2"))
    finally:
        await store.close()


# ---------- lookup order for a new run ----------
async def make_service(tmp_path, packs):
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    provider = FakeProvider(settings, delay_s=0)
    service = ResearchService(settings, store, provider, FakeSearchAdapter(), live_families(packs))
    return settings, store, service


async def test_lookup_order_research_id_then_exact_key_then_nearest_knobs(tmp_path, packs) -> None:
    settings, store, service = await make_service(tmp_path, packs)
    try:
        knobs = live_families(packs)["add8"]
        assert knob_distance(knobs, {"width": 16}, {"width": 20}) == pytest.approx(4 / 28)
        await store.put_research(research("rs_20", "add8", {"width": 20}, techniques=["kogge_stone"]))
        await store.put_research(research("rs_16", "add8", {"width": 16}, techniques=["carry_select"]))
        await store.put_research(research("rs_label", "add8", {"width": 4}, key=False, techniques=["brent_kung"]))
        await store.grant_research("rs_label", session_hmac("owner"))

        async def refs(width: int, rid: str | None = None, session: str = "owner") -> RunReferences:
            return await service.references_for_run(
                family="add8", params={"width": width}, priority="area", research_id=rid, session_id=session,
                pack_sha256="p", tool_image_digest="i",
            )

        assert (await refs(16)).research_id == "rs_16"  # exact key
        assert (await refs(18)).research_id in ("rs_16", "rs_20")  # nearest: 2/28 each way
        assert (await refs(22)).research_id == "rs_20"  # nearest within 0.25
        assert (await refs(32)).research_id is None  # 12/28 from the nearest: too far
        assert (await refs(4, "rs_label")).research_id == "rs_label"  # owned and the same chip and knobs
        assert (await refs(4, "rs_label", session="other")).research_id is None  # not owned: ignored, not an error
        assert (await refs(8, "rs_label")).research_id is None  # another knob setting: ignored (and 8 is too far)
        assert (await refs(4, "rs_missing")).research_id is None
    finally:
        await store.close()


async def test_planner_and_explore_get_only_our_own_labels(tmp_path, packs) -> None:
    refs = RunReferences(key="k", research_id="rs", techniques=["carry_select", "kogge_stone"], past_lines=["- 1 earlier run(s), 1 finished."])
    text = refs.planner_text()
    assert "Carry-select adder" in text and "Kogge-Stone adder" in text and "1 earlier run(s)" in text
    assert "http" not in text and "arxiv" not in text
    assert refs.explore_idea({"carry_select"}).id == "kogge_stone"  # type: ignore[union-attr]
    assert refs.explore_idea({"carry_select", "kogge_stone"}) is None
    assert RunReferences(key="k").planner_text() == ""


class Recording:
    name = "recording"

    def __init__(self, settings) -> None:
        self.fake = FakeProvider(settings, delay_s=0, cassette_dir=tmp_cassettes(settings))
        self.requests = []

    async def complete(self, req):  # noqa: ANN001, ANN201
        self.requests.append(req)
        return await self.fake.complete(req)

    async def aclose(self) -> None:
        return None


async def test_optimizer_explore_uses_the_idea_text(tmp_path, packs) -> None:
    from verity.core.budget import BudgetLimits
    from verity.llm.gateway import RunLLM
    from verity.runs.ledger import RunLedger
    from verity.contracts import PPA

    settings = make_settings(tmp_path / "data")
    provider = Recording(settings)
    llm = RunLLM(provider, settings, RunLedger(BudgetLimits.from_settings(settings), settings))
    pack = packs["add8"]
    ppa = PPA(area_um2=10.0, clock_period_ns=1.0, pdk="nangate45")
    await optimize(llm, pack, pack.reference_rtl() or "", ppa, ppa, "area", "EXPLORE", idea="Kogge-Stone adder: a tree.")
    await optimize(llm, pack, pack.reference_rtl() or "", ppa, ppa, "area", "EXPLORE")
    with_idea, generic = provider.requests
    assert "Kogge-Stone adder: a tree." in with_idea.user + with_idea.system
    assert "different micro-architecture" in generic.user + generic.system


# ---------- outcomes from both endings, researchId through POST /runs ----------
async def make_manager(tmp_path, packs, *, delay_s: float = 0.0):
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    provider = Recording(settings)
    research_service = ResearchService(settings, store, provider, FakeSearchAdapter(), live_families(packs))
    manager = RunManager(settings, store, provider, FakeToolRunner(delay_s=delay_s), packs, research=research_service)
    await manager.start()
    return settings, store, manager, provider


async def drain(manager, run_id):
    return [e async for e in manager.stream(run_id, 0)]


async def test_a_finished_run_writes_one_outcome_and_a_new_run_reads_it(tmp_path, packs) -> None:
    settings, store, manager, provider = await make_manager(tmp_path, packs)
    try:
        reply = await manager.parse("please make a 16-bit adder, smallest area", session_id="s1")
        research_id = reply.research.id  # type: ignore[union-attr]
        run = await manager.create_run(
            session_id="s1", preset_id="add8", priority="area", mode="full", params={"width": 16}, research_id=research_id
        )
        assert run.research_id == research_id
        await asyncio.wait_for(drain(manager, run.id), timeout=60)
        outcome = await store.get_run_outcome(run.id)
        assert outcome is not None and outcome.status == "done" and not outcome.partial
        assert outcome.research_id == research_id and outcome.key == canonical_key("add8", {"width": 16})
        assert outcome.v1_area_um2 == 1000.0 and outcome.versions >= 4 and outcome.winner_id is not None
        if outcome.winner_id != "c1":  # the fake tool runner measures children smaller than v1
            assert outcome.winner_area_rel is not None and outcome.winner_area_rel < 1.0
        strategies = await store.strategy_outcomes(outcome.key, outcome.pack_sha256, outcome.tool_image_digest)
        assert {s.strategy for s in strategies} <= {"SIMPLIFY", "OPTIMIZE", "EXPLORE"} and strategies
        explore = [s for s in strategies if s.strategy == "EXPLORE"]
        assert explore and explore[0].technique in {"ripple_carry", "carry_lookahead", "carry_select", "kogge_stone", "brent_kung"}
        rows = await store._fetchall("SELECT COUNT(*) FROM run_outcome WHERE run_id = ?", (run.id,))
        assert rows[0][0] == 1  # written twice (orchestrator, then manager), one row

        # What reached the Nemotron roles: technique labels and code numbers only, never web or user text.
        plan_req = next(r for r in provider.requests if r.role == "planner")
        assert "Known approaches" in plan_req.user and "please make" not in plan_req.user + plan_req.system
        nemotron = [r for r in provider.requests if r.role in ("planner", "rtl_gen", "repair", "optimizer")]
        sources = (await store.get_research(research_id)).sources  # type: ignore[union-attr]
        for req in nemotron:
            for src in sources:
                assert src.url not in req.user and (not src.snippet or src.snippet not in req.user)
        explore_req = next(r for r in provider.requests if r.role == "optimizer" and r.tags.get("strategy") == "EXPLORE")
        assert explore_req.tags.get("idea") == "1"

        again = await manager.parse("a 16-bit adder", session_id="s1")
        assert again.research is not None and again.research.past_runs == 1  # same family, knobs, pack and image
        second = await manager.create_run(session_id="s1", preset_id="add8", priority="area", mode="fast", params={"width": 16})
        await asyncio.wait_for(drain(manager, second.id), timeout=60)
        second_plan = [r for r in provider.requests if r.role == "planner"][-1]
        assert "Past Verity runs on this exact design" in second_plan.user and "1 earlier run(s)" in second_plan.user
    finally:
        await manager.shutdown()
        await store.close()


async def test_a_stopped_run_writes_a_partial_outcome(tmp_path, packs) -> None:
    settings, store, manager, _ = await make_manager(tmp_path, packs, delay_s=0.3)
    try:
        run = await manager.create_run(session_id="s1", preset_id="add8", priority="area", mode="full")
        await asyncio.sleep(0.5)
        await manager.stop(run.id, "s1")
        await asyncio.wait_for(drain(manager, run.id), timeout=60)
        outcome = await store.get_run_outcome(run.id)
        assert outcome is not None and outcome.status == "stopped" and outcome.partial
        assert outcome.key == canonical_key("add8", {"width": 8})
    finally:
        await manager.shutdown()
        await store.close()


async def test_outcome_writer_is_idempotent(tmp_path, packs) -> None:
    from verity_test_support import execute_fake_run

    settings = make_settings(tmp_path / "data")
    pack = packs["add8"]
    fake = await execute_fake_run(settings, pack, mode="fast", on_ctx=lambda ctx: setattr(ctx, "outcome_key", "k-add8"))
    try:
        first = await fake.store.get_run_outcome(fake.run_id)
        assert first is not None and first.status == "done"
        await record_run_outcome(fake.store, fake.ctx, status="done", winner_id=first.winner_id)
        await record_run_outcome(fake.store, fake.ctx, status="done", winner_id=first.winner_id)
        rows = await fake.store._fetchall("SELECT COUNT(*) FROM run_outcome")
        assert rows[0][0] == 1
        again = await fake.store.get_run_outcome(fake.run_id)
        assert again is not None and again.created_at == first.created_at and again.winner_id == first.winner_id
    finally:
        await fake.store.close()


def test_post_runs_passes_research_id_to_the_service(tmp_path, packs) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    from test_sse_and_routes import StubService

    class Capture(StubService):
        seen: dict = {}

        async def create_run(self, **kwargs):  # noqa: ANN003, ANN201
            Capture.seen = kwargs
            return await super().create_run(**{k: v for k, v in kwargs.items() if k != "research_id"})

    settings = make_settings(tmp_path / "data")
    service = Capture(settings, packs)
    with TestClient(create_app(settings, service)) as client:  # type: ignore[arg-type]
        res = client.post("/runs", json={"presetId": "add8", "priority": "area", "researchId": "rs_abc"})
        assert res.status_code == 201 and Capture.seen["research_id"] == "rs_abc"
        res = client.post("/runs", json={"presetId": "add8", "priority": "area", "researchId": "x" * 200})
        assert res.status_code in (201, 429) and "research_id" not in Capture.seen  # too long: dropped, not an error
