"""GET /runs adds each run's outcome (from run_outcome) and, when the winner has one, its sign-off seeds line.

The store tests read the SQL join and final/signoff.json; the route test checks the wire shape (camelCase, nulls where
the ratio is unknown, `outcome` and `signoff` left out when absent) through the real composition root."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from verity import main as main_module
from verity.contracts import PPA, Candidate, to_json
from verity.contracts.signoff import SeedTally, Signoff, SignoffLint, SignoffTests
from verity.core.budget import BudgetLimits
from verity.core.records import RunOutcomeRow, RunRecord
from verity.store import SqliteStore
from verity_test_support import make_settings


def make_run(settings, run_id: str, *, status: str = "done", winner: str | None = "c2") -> RunRecord:
    return RunRecord(
        id=run_id,
        session_id="s1",
        preset_id="mac4",
        priority="area",
        mode="fast",
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        budget=BudgetLimits.from_settings(settings),
        task_pack_sha256="a" * 64,
        title=f"title {run_id}",
        winner_id=winner,
    )


def outcome_row(run_id: str, **over) -> RunOutcomeRow:
    base = dict(
        run_id=run_id, family="mac4", params={}, key="k", priority="area", mode="fast", status="done", partial=False,
        pack_sha256="a" * 64, tool_image_digest="d", winner_id="c2", winner_label="v2", winner_area_rel=0.9842,
        winner_clock_rel=0.926, winner_power_rel=None, versions=4, usd=0.1875, created_at=datetime.now(UTC),
    )
    base.update(over)
    return RunOutcomeRow(**base)


def sheet(candidate_id: str, run: int = 9, passed: int = 9) -> bytes:
    body = Signoff(
        candidate_id=candidate_id,
        tests=SignoffTests(seeds_run=run, seeds_passed=passed, per_seed=[SeedTally(seed=1, passed=16, total=16)]),
        lint=SignoffLint(errors=0, warnings=0, latches=0),
        eqy_vs_v1="proven",
        eqy_vs_human="proven",
        strength=None,
        duration_ms=10,
    )
    return to_json(body).encode("utf-8")


async def seed(settings) -> None:
    """r_full: outcome + sheet. r_nosheet: outcome only. r_old: no outcome row (ended before run_outcome existed).
    r_other: a sheet for a version that is not the winner. r_stopped: outcome without a winner."""
    store = SqliteStore(settings)
    await store.open()
    try:
        for rid in ("r_full", "r_nosheet", "r_old", "r_other"):
            await store.create_run(make_run(settings, rid))
            await store.upsert_candidate(
                rid,
                Candidate(id="c2", label="v2", round=1, created_by="SIMPLIFY", reason="r", rtl="module m; endmodule",
                          status="measured", ppa=PPA(area_um2=100.0, clock_period_ns=2.0, pdk="Nangate45")),
            )
        await store.create_run(make_run(settings, "r_stopped", status="stopped", winner=None))
        await store.put_run_outcome(outcome_row("r_full"))
        await store.put_run_outcome(outcome_row("r_nosheet"))
        await store.put_run_outcome(outcome_row("r_other"))
        await store.put_run_outcome(
            outcome_row("r_stopped", status="stopped", partial=True, winner_id=None, winner_label=None,
                        winner_area_rel=None, winner_clock_rel=None, versions=1, usd=0.02)
        )
        await store.write_run_file("r_full", "final/signoff.json", sheet("c2"), "application/json")
        await store.write_run_file("r_other", "final/signoff.json", sheet("c9"), "application/json")
    finally:
        await store.close()


async def test_list_runs_carries_the_outcome_and_the_signoff_seeds(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    await seed(settings)
    store = SqliteStore(settings)
    await store.open()
    try:
        items = {i.id: i for i in await store.list_runs(limit=20)}
    finally:
        await store.close()

    full = items["r_full"].outcome
    assert full is not None
    assert (full.area_vs_v1, full.clock_vs_v1, full.power_vs_v1) == (0.9842, 0.926, None)
    assert full.versions == 4 and full.usd == pytest.approx(0.1875)
    assert full.signoff is not None and (full.signoff.seeds_passed, full.signoff.seeds_run) == (9, 9)

    assert items["r_nosheet"].outcome is not None and items["r_nosheet"].outcome.signoff is None
    assert items["r_other"].outcome is not None and items["r_other"].outcome.signoff is None  # a sheet for another version
    assert items["r_old"].outcome is None  # no run_outcome row: the field is simply absent
    stopped = items["r_stopped"].outcome
    assert stopped is not None and stopped.area_vs_v1 is None and stopped.clock_vs_v1 is None and stopped.signoff is None


async def test_a_sheet_that_ran_no_seeds_or_cannot_be_read_is_left_out(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        for rid in ("r_zero", "r_junk"):
            await store.create_run(make_run(settings, rid))
            await store.put_run_outcome(outcome_row(rid))
        await store.write_run_file("r_zero", "final/signoff.json", sheet("c2", run=0, passed=0), "application/json")
        await store.write_run_file("r_junk", "final/signoff.json", b"{not json", "application/json")
        items = {i.id: i for i in await store.list_runs(limit=20)}
    finally:
        await store.close()
    assert items["r_zero"].outcome is not None and items["r_zero"].outcome.signoff is None
    assert items["r_junk"].outcome is not None and items["r_junk"].outcome.signoff is None


def test_get_runs_wire_shape(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from verity.tools.local_docker import LocalDockerRunner

    async def sandbox_ok(self) -> None:
        return None

    monkeypatch.setattr(LocalDockerRunner, "_check_sandbox", sandbox_ok)
    settings = make_settings(tmp_path / "data")
    asyncio.run(seed(settings))
    with TestClient(main_module.build_app(settings)) as client:
        res = client.get("/runs")  # public: no session needed
        assert res.status_code == 200
        by_id = {r["id"]: r for r in res.json()["runs"]}

    assert by_id["r_full"]["outcome"] == {
        "areaVsV1": 0.9842,
        "clockVsV1": 0.926,
        "powerVsV1": None,
        "versions": 4,
        "usd": 0.1875,
        "signoff": {"seedsPassed": 9, "seedsRun": 9},
    }
    assert "signoff" not in by_id["r_nosheet"]["outcome"]
    assert "outcome" not in by_id["r_old"]
    assert by_id["r_old"]["winner"]["label"] == "v2"  # the rest of the item is unchanged
    assert set(by_id["r_old"]) == {"id", "presetId", "params", "title", "status", "finishedAt", "winner"}
