"""SqliteStore and the write-once artifact files (ARCHITECTURE 7 and 15)."""

from datetime import UTC, datetime, timedelta

import pytest

from verity.contracts import PPA, Attempt, Candidate, CaptionEvent, RunPhaseEvent, Verification
from verity.core.budget import BudgetLimits, SpendSnapshot
from verity.core.errors import NotFound
from verity.core.records import AttemptMeta, EvalResultRow, RunPatch, RunRecord
from verity.store import DuplicateEvent, SqliteStore
from verity.store.files import check_relpath


@pytest.fixture()
async def store(settings):
    s = SqliteStore(settings)
    await s.open()
    yield s
    await s.close()


def make_run(settings, run_id: str = "r_1", session: str = "s1", status: str = "queued") -> RunRecord:
    return RunRecord(
        id=run_id,
        session_id=session,
        preset_id="mac4",
        priority="area",
        mode="fast",
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
        budget=BudgetLimits.from_settings(settings),
        task_pack_sha256="a" * 64,
    )


async def test_run_round_trip_and_partial_update(store, settings) -> None:
    await store.create_run(make_run(settings))
    got = await store.get_run("r_1")
    assert got is not None and got.status == "queued" and got.budget.max_usd == settings.budget.run.max_usd
    assert await store.get_run("nope") is None

    started = datetime.now(UTC)
    await store.update_run("r_1", RunPatch(status="running", started_at=started))
    await store.update_run("r_1", RunPatch(spent=SpendSnapshot(llm_calls=2, usd=0.01), prompt_versions={"planner": "p@1"}))
    got = await store.get_run("r_1")
    assert got is not None and got.status == "running"  # the second patch did not touch status
    assert got.started_at is not None and got.spent.llm_calls == 2 and got.prompt_versions == {"planner": "p@1"}

    await store.update_run("r_1", RunPatch(status="error", error_reason="boom", title="Custom Title", params={"width": 16}))
    got_err = await store.get_run("r_1")
    assert got_err is not None and got_err.error_reason == "boom"
    assert got_err.title == "Custom Title"
    assert got_err.params == {"width": 16}
    with pytest.raises(NotFound):
        await store.update_run("missing", RunPatch(status="done"))


async def test_run_with_params_and_title_round_trip(store, settings) -> None:
    run = make_run(settings, "r_params", "s1", "running")
    run.params = {"lanes": 6, "width": 6, "cycles": 20}
    run.title = "6-lane MAC"
    await store.create_run(run)
    got = await store.get_run("r_params")
    assert got is not None
    assert got.title == "6-lane MAC"
    assert got.params == {"lanes": 6, "width": 6, "cycles": 20}


async def test_counts_and_status_queries(store, settings) -> None:
    await store.create_run(make_run(settings, "r_1", "s1", "running"))
    await store.create_run(make_run(settings, "r_2", "s1", "done"))
    await store.create_run(make_run(settings, "r_3", "s2", "queued"))
    assert {r.id for r in await store.runs_with_status(["queued", "running"])} == {"r_1", "r_3"}
    assert await store.runs_with_status([]) == []
    assert await store.count_runs(session_id="s1") == 2
    assert await store.count_runs(statuses=["running"], session_id="s1") == 1
    assert await store.count_runs(created_since=datetime.now(UTC) + timedelta(hours=1)) == 0


async def test_events_are_append_only_and_ordered(store, settings) -> None:
    await store.create_run(make_run(settings))
    e1 = RunPhaseEvent(phase="running", seq=1, t_ms=0)
    e2 = CaptionEvent(text="hi", chapter="Spec", seq=2, t_ms=5)
    await store.append_event("r_1", e2)
    await store.append_event("r_1", e1)
    with pytest.raises(DuplicateEvent):
        await store.append_event("r_1", e1)
    events = await store.events_after("r_1", 0)
    assert [e.seq for e in events] == [1, 2] and isinstance(events[1], CaptionEvent)
    assert [e.seq for e in await store.events_after("r_1", 1)] == [2]
    assert await store.last_seq("r_1") == 2 and await store.last_seq("r_none") == 0


async def test_candidate_upsert_and_attempt_replace(store, settings) -> None:
    await store.create_run(make_run(settings))
    candidate = Candidate(
        id="c1", label="v1", round=0, created_by="INIT", reason="r", rtl="module m; endmodule", status="measured",
        ppa=PPA(area_um2=10.0, pdk="Nangate45"),
    )
    await store.upsert_candidate("r_1", candidate)
    await store.upsert_candidate("r_1", candidate.model_copy(update={"is_elite": True, "status": "pass"}))

    verification = Verification(lint="pass", passed=1, total=2, tool="t", duration_ms=1)
    attempt = Attempt(n=1, by="INIT", reason="first", rtl="x", verification=verification)
    meta = AttemptMeta(model_id="m", prompt_version="p@1", input_tokens=10, output_tokens=5, created_at=datetime.now(UTC))
    await store.add_attempt("r_1", "c1", attempt, meta)
    # Adding the same attempt again (a diagnosis attached later) replaces the verification, keeps unset meta.
    diagnosed = attempt.model_copy(update={"verification": verification.model_copy(update={"diagnosis": "why"})})
    await store.add_attempt("r_1", "c1", diagnosed, AttemptMeta(created_at=datetime.now(UTC)))


async def test_daily_spend_adds_and_validates_the_day(store) -> None:
    assert await store.daily_spend("2026-10-05") == 0.0
    await store.add_daily_spend("2026-10-05", 0.25)
    await store.add_daily_spend("2026-10-05", 0.5)
    assert await store.daily_spend("2026-10-05") == pytest.approx(0.75)
    with pytest.raises(ValueError):
        await store.add_daily_spend("yesterday", 1.0)


async def test_run_files_are_write_once_and_path_checked(store, settings) -> None:
    await store.create_run(make_run(settings))
    ref = await store.write_run_file("r_1", "candidates/c1/attempt-1.v", b"module m; endmodule\n", "text/x-verilog")
    assert ref.size_bytes == 20  # len of the content above
    assert await store.read_run_file("r_1", "candidates/c1/attempt-1.v") == b"module m; endmodule\n"
    await store.write_run_file("r_1", "candidates/c1/attempt-1.v", b"module m; endmodule\n", "text/x-verilog")  # same: ok
    with pytest.raises(FileExistsError):
        await store.write_run_file("r_1", "candidates/c1/attempt-1.v", b"different", "text/plain")
    assert await store.read_run_file("r_1", "missing.txt") is None
    for bad in ("../x", "a/../b", "/abs", "a//b", "a b", "..", ".", "a/./b", "x" * 300):
        with pytest.raises(ValueError):
            check_relpath(bad)
    with pytest.raises(ValueError):
        await store.write_run_file("../r_1", "x.txt", b"1", "text/plain")


async def test_blobs_are_deduplicated_and_eval_rows_stored(store) -> None:
    a = await store.put_blob(b"same", "text/plain")
    b = await store.put_blob(b"same", "text/plain")
    assert a.sha256 == b.sha256 and a.path == b.path
    await store.add_eval_result(
        EvalResultRow(eval_run_id="e_1", task_id="mac4", suite="packs", metrics={"ok": True}, created_at=datetime.now(UTC))
    )


async def test_store_list_runs_with_winner(store, settings) -> None:
    run = make_run(settings, "r_winner", "s1", "done")
    run.params = {"lanes": 4, "width": 8}
    run.title = "4-lane MAC Array"
    run.winner_id = "c1"
    run.ended_at = datetime.now(UTC)
    await store.create_run(run)

    candidate = Candidate(
        id="c1",
        label="v1",
        parent_id=None,
        round=0,
        created_by="INIT",
        status="measured",
        reason="Baseline",
        rtl="module m; endmodule",
        ppa=PPA(area_um2=1840.5, clock_period_ns=3.2, pdk="Nangate45"),
    )
    await store.upsert_candidate("r_winner", candidate)

    runs = await store.list_runs(limit=10)
    matching = [r for r in runs if r.id == "r_winner"]
    assert len(matching) == 1
    item = matching[0]
    assert item.preset_id == "mac4"
    assert item.params == {"lanes": 4, "width": 8}
    assert item.title == "4-lane MAC Array"
    assert item.status == "done"
    assert item.winner is not None
    assert item.winner.label == "v1"
    assert item.winner.area_um2 == pytest.approx(1840.5)
    assert item.winner.clock_period_ns == pytest.approx(3.2)
