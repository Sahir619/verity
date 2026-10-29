"""A full orchestrator run with the fake provider and the fake tool runner (no network, no sandbox).

This is the first thing to run on test day: it exercises the real orchestrator, roles, gateway, ledger, event hub and
SQLite store end to end and checks the event stream against the rules the web app depends on (golden.json shape).
"""

import json
from itertools import pairwise
from pathlib import Path

import pytest

from verity.contracts import (
    ArtifactEvent,
    CandidateEvent,
    CaptionEvent,
    CreditsEvent,
    GateEvent,
    HumanEvent,
    OpEvent,
    PlanEvent,
    RoundEvent,
    RunPhaseEvent,
    StageEvent,
    WinnerEvent,
)
from verity.contracts.view_types import PinSteer, ToggleSteer, WinnerOverrideSteer
from verity.core.pack import TaskPack
from verity_test_support import FakeRun, FakeToolRunner, execute_fake_run, make_settings

pytestmark = pytest.mark.asyncio


def final_candidates(events) -> dict:
    final = {}
    for e in events:
        if isinstance(e, CandidateEvent):
            final[e.candidate.id] = e.candidate
    return final


def assert_stream_is_well_formed(events) -> None:
    assert [e.seq for e in events] == list(range(1, len(events) + 1)), "seq must be gap-free from 1"
    assert all(a.t_ms <= b.t_ms for a, b in pairwise(events)), "tMs must not decrease"
    phases = [e.phase for e in events if isinstance(e, RunPhaseEvent)]
    assert phases[0] == "running" and len(phases) == 2 and phases[1] in ("done", "cap_reached", "error")


def assert_canvas_is_consistent(events) -> None:
    """Same rules as frontend/scripts/validate-fixture.ts: connects follow add_node, no repeated ids, <= 12 content nodes
    for a 4-block plan, and a candidate is added to the canvas before its first candidate event."""
    added: set[str] = set()
    for e in events:
        if isinstance(e, OpEvent):
            op = e.op
            if op.op == "add_node":
                assert op.id not in added, f"add_node repeats {op.id}"
                added.add(op.id)
            elif op.op == "connect":
                assert op.source in added and op.target in added, f"connect before add_node: {op.source}->{op.target}"
        if isinstance(e, CandidateEvent):
            assert e.candidate.id in added, f"candidate {e.candidate.id} upserted before add_node"
    assert len(added) <= 12 + 4, "far more canvas nodes than the web expects"


def assert_stages_settle(events) -> None:
    last: dict[str, str] = {}
    for e in events:
        if isinstance(e, StageEvent):
            last[e.stage] = e.status
    assert "active" not in last.values(), f"a stage was left active: {last}"


async def finish(run: FakeRun) -> None:
    await run.store.close()


async def test_full_run_end_to_end(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="full")
    try:
        assert run.error is None, run.error
        assert run.outcome is not None and run.outcome.status == "done", run.outcome
        events = run.events
        assert_stream_is_well_formed(events)
        assert_canvas_is_consistent(events)
        assert_stages_settle(events)

        # golden.json ends with a `done` caption AFTER `run done`. The sign-off follows them (CONTRACTS 6.3): the
        # winner's waveform, signoff/<id>.json, then the sign-off caption, also in the Done chapter.
        done_at = next(i for i, e in enumerate(events) if isinstance(e, RunPhaseEvent) and e.phase == "done")
        assert isinstance(events[done_at + 1], CaptionEvent) and events[done_at + 1].text.startswith("Done.")
        tail = events[done_at + 2 :]
        paths = [e.path for e in tail if isinstance(e, ArtifactEvent)]
        winner_id = [e for e in events if isinstance(e, WinnerEvent)][-1].candidate_id
        assert paths == [f"waves/{winner_id}.json", f"signoff/{winner_id}.json"], paths
        assert isinstance(events[-1], CaptionEvent) and events[-1].chapter == "Done"
        assert events[-1].text.startswith("Sign-off for ")

        stage_order = [e.stage for e in events if isinstance(e, StageEvent) and e.status == "active"]
        for needed in ("plan", "lock", "rtl", "lint", "sim", "fix", "synth", "evolve"):
            assert needed in stage_order, f"stage {needed} never became active: {stage_order}"
        assert stage_order.index("plan") < stage_order.index("lock") < stage_order.index("rtl") < stage_order.index("synth")

        gates = [e.state for e in events if isinstance(e, GateEvent)]
        assert gates[0] == "open" and gates[-1] == "closed"

        final = final_candidates(events)
        c1 = final["c1"]
        assert c1.is_baseline and c1.status == "measured"
        assert c1.fitness is not None and c1.fitness.score == 0.0 and c1.fitness.baseline_id == "c1"
        assert c1.ppa is not None and c1.ppa.power_mw is None  # power is not measured in the MVP
        # The fake provider injects exactly one bug into the first RTL, so the FIX path ran once.
        assert c1.fix_count == 1 and [a.by for a in c1.attempts] == ["INIT", "FIX"]
        assert c1.attempts[0].verification.passed < c1.attempts[0].verification.total
        assert c1.attempts[0].verification.diagnosis, "the repair diagnosis is attached to the failed attempt"
        assert c1.attempts[1].verification.passed == c1.attempts[1].verification.total

        assert len(final) >= 4, "round 1 makes three children of v1"
        for cand in final.values():
            if cand.status == "measured":
                assert cand.fitness is not None
            else:
                assert cand.fitness is None, f"{cand.id} has fitness but status {cand.status}"
            assert not (cand.status == "rejected" and cand.is_elite)

        rounds = [e for e in events if isinstance(e, RoundEvent)]
        assert rounds and rounds[0].round == 1 and rounds[0].parents == ["c1"]
        assert any(e.elites for e in rounds)

        winners = [e for e in events if isinstance(e, WinnerEvent)]
        winner = winners[-1]
        assert winner.candidate_id in final and final[winner.candidate_id].status == "measured"
        if winner.candidate_id != "c1":
            assert final[winner.candidate_id].eqy is not None and final[winner.candidate_id].eqy.status == "proven"
            proofs = [e for e in events if isinstance(e, OpEvent) and e.op.op == "connect" and e.op.kind == "proof"]
            assert proofs and proofs[0].op.target == "c1"
            assert run.tools.eqy_calls >= 1

        credits = [e.used_usd for e in events if isinstance(e, CreditsEvent)]
        assert credits and credits == sorted(credits)
        paths = {e.path for e in events if isinstance(e, ArtifactEvent)}
        for needed in ("tests/golden_model.py", "tests/test_dut.py", "plan/plan.r1.json", "reports/synth_v1.txt", "reports/summary.json"):
            assert needed in paths, f"artifact {needed} missing: {sorted(paths)}"

        # Store side: run record, files on disk.
        record = await run.store.get_run(run.run_id)
        assert record is not None and {"planner", "rtl_gen", "repair"} <= set(record.prompt_versions)
        assert all("@" in v for v in record.prompt_versions.values())
        assert await run.store.read_run_file(run.run_id, "spec.md")
        assert await run.store.read_run_file(run.run_id, "candidates/c1/attempt-1.v")
        summary = await run.store.read_run_file(run.run_id, "final/summary.json")
        assert summary and json.loads(summary)["outcome"] == "done"
    finally:
        await finish(run)


async def test_fast_mode_skips_evolve_and_prove(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast")
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        assert_stream_is_well_formed(run.events)
        skipped = {e.stage for e in run.events if isinstance(e, StageEvent) and e.status == "skipped"}
        assert {"evolve", "prove"} <= skipped
        assert set(final_candidates(run.events)) == {"c1"}
        assert [e.candidate_id for e in run.events if isinstance(e, WinnerEvent)] == ["c1"]
        assert run.tools.eqy_calls == 1  # only the human_ref check; no prove stage in fast mode
    finally:
        await finish(run)


async def test_pin_at_the_gate_replans_and_is_enforced_in_code(tmp_path: Path, mac4: TaskPack) -> None:
    settings = make_settings(tmp_path / "data", gate_ms=400)
    pin = PinSteer(block_id="blk-mult", pin="Minimize area")
    run = await execute_fake_run(settings, mac4, mode="fast", steers=(pin, ToggleSteer(block_id="blk-mult", variant_id="pipeline", value="2-stage")))
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        plans = [e for e in run.events if isinstance(e, PlanEvent)]
        assert [p.revision for p in plans] == [1, 2]
        assert plans[1].diff is not None and "blk-mult" in plans[1].diff.changed
        mult = next(b for b in plans[1].blocks if b.id == "blk-mult")
        assert mult.pins == ["Minimize area"]
        humans = [e.input.kind for e in run.events if isinstance(e, HumanEvent)]
        assert humans[0] == "spec" and "pin" in humans
        captions = [e.text for e in run.events if isinstance(e, CaptionEvent)]
        assert any(t.startswith('You pinned "Minimize area"') for t in captions)
        assert any(t.startswith("Plan updated.") for t in captions)
        statuses = [(e.op.id, e.op.status) for e in run.events if isinstance(e, OpEvent) and e.op.op == "update_status"]
        assert ("blk-mult", "replanning") in statuses and ("blk-mult", "drafted") in statuses
    finally:
        await finish(run)


async def test_plan_with_wrong_latency_is_rejected_before_any_verilog(tmp_path: Path, mac4: TaskPack) -> None:
    cassettes = tmp_path / "cassettes" / "mac4"
    cassettes.mkdir(parents=True)
    bad_plan = {
        "summary": "Wrong latency",
        "blocks": [{"id": "x", "name": "Only block", "reason": "r", "pinOptions": [], "pins": [], "variants": []}],
        "latencyCycles": 3,
        "estimatedLines": 40,
    }
    (cassettes / "planner.txt").write_text(json.dumps(bad_plan), encoding="utf-8")
    run = await execute_fake_run(make_settings(tmp_path / "data"), mac4, cassette_dir=tmp_path / "cassettes")
    try:
        assert run.outcome is not None and run.outcome.status == "error"
        assert "plan rejected" in (run.outcome.error_reason or "")
        stages = {(e.stage, e.status) for e in run.events if isinstance(e, StageEvent)}
        assert ("plan", "failed") in stages and not any(s == "rtl" for s, _ in stages)
        assert any(isinstance(e, CaptionEvent) and e.text.startswith("Plan rejected before any Verilog") for e in run.events)
        assert run.tools.verify_calls == 0
        assert isinstance(run.events[-1], RunPhaseEvent) and run.events[-1].phase == "error"
    finally:
        await finish(run)


async def test_rtl_that_breaks_the_rules_never_reaches_the_sandbox(tmp_path: Path, mac4: TaskPack) -> None:
    reference = mac4.reference_rtl() or ""
    banned = reference.replace("endmodule", "    initial begin end\nendmodule", 1)
    cassettes = tmp_path / "cassettes" / "mac4"
    cassettes.mkdir(parents=True)
    (cassettes / "rtl_gen.txt").write_text(f"REASON: first try\n\n```verilog\n{banned}```\n", encoding="utf-8")
    tools = FakeToolRunner()
    run = await execute_fake_run(make_settings(tmp_path / "data"), mac4, mode="fast", tools=tools, cassette_dir=tmp_path / "cassettes")
    try:
        assert run.outcome is not None and run.outcome.status == "done", run.outcome
        c1 = final_candidates(run.events)["c1"]
        first = c1.attempts[0].verification
        assert first.lint == "fail" and first.tool == "Verity RTL rules"
        assert (first.lint_log or "").startswith("Rejected before simulation:")
        assert c1.fix_count == 1 and c1.attempts[1].by == "FIX"
        assert tools.verify_calls == 1, "only the repaired attempt may reach the sandbox"
        assert any(isinstance(e, CaptionEvent) and "broke the coding rules" in e.text for e in run.events)
    finally:
        await finish(run)


async def test_budget_cut_before_evolve_ends_as_cap_reached_with_v1_as_winner(tmp_path: Path, mac4: TaskPack) -> None:
    base = make_settings(tmp_path / "data")
    tight = base.budget.model_copy(update={"run": base.budget.run.model_copy(update={"max_llm_calls": 3})})
    run = await execute_fake_run(base.model_copy(update={"budget": tight}), mac4, mode="full")
    try:
        assert run.error is None
        assert run.outcome is not None and run.outcome.status == "cap_reached", run.outcome
        assert run.outcome.winner_id == "c1"
        phases = [e.phase for e in run.events if isinstance(e, RunPhaseEvent)]
        assert phases[-1] == "cap_reached"
        assert any(isinstance(e, CaptionEvent) and e.text.startswith("Budget reached") for e in run.events)
        assert set(final_candidates(run.events)) == {"c1"}
        assert isinstance(run.events[-1], CaptionEvent) and run.events[-1].chapter == "Done"
    finally:
        await finish(run)


async def test_budget_cut_before_a_baseline_exists_is_cap_reached_without_winner(tmp_path: Path, mac4: TaskPack) -> None:
    base = make_settings(tmp_path / "data")
    tight = base.budget.model_copy(update={"run": base.budget.run.model_copy(update={"max_llm_calls": 2})})
    run = await execute_fake_run(base.model_copy(update={"budget": tight}), mac4, mode="full")
    try:
        assert run.outcome is not None and run.outcome.status == "cap_reached", run.outcome
        assert run.outcome.winner_id is None
        assert [e for e in run.events if isinstance(e, RunPhaseEvent)][-1].phase == "cap_reached"
    finally:
        await finish(run)


async def test_winner_override_after_the_ai_pick(settings, mac4: TaskPack) -> None:
    """A steer queued during the prove stage is applied after it: the human choice becomes the final winner."""
    holder: dict = {}
    tools = FakeToolRunner()
    original = tools.check_equivalence

    async def eqy_then_override(gold, gate, pack):
        result = await original(gold, gate, pack)
        holder["ctx"].steer.put_nowait(WinnerOverrideSteer(candidate_id="c1"))
        return result

    tools.check_equivalence = eqy_then_override  # type: ignore[method-assign]
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools, on_ctx=lambda ctx: holder.update(ctx=ctx))
    try:
        if tools.eqy_calls <= 1:  # only the human_ref check ran
            pytest.skip("the AI picked v1, so the prove stage (and the override) was not exercised")
        winners = [e for e in run.events if isinstance(e, WinnerEvent)]
        assert winners[-1].by == "user" and winners[-1].candidate_id == "c1"
        assert winners[-1].ai_pick_id != "c1"
        overrides = [e for e in run.events if isinstance(e, HumanEvent) and e.input.kind == "winner_override"]
        assert overrides
    finally:
        await finish(run)


async def test_not_equivalent_demotes_the_winner(settings, mac4: TaskPack) -> None:
    tools = FakeToolRunner(eqy_status="not_equivalent")
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools)
    try:
        assert run.error is None and run.outcome is not None
        if tools.eqy_calls <= 1:  # only the human_ref check ran
            pytest.skip("the AI picked v1, so EQY never ran")
        final = final_candidates(run.events)
        assert run.outcome.winner_id == "c1" or final[run.outcome.winner_id].status == "measured"
        assert any(c.status == "rejected" and c.eqy is not None and c.eqy.status == "not_equivalent" for c in final.values())
        assert tools.eqy_calls <= mac4_prove_attempts(settings) + 1  # +1: the human_ref check
    finally:
        await finish(run)


def mac4_prove_attempts(settings) -> int:
    return settings.evolution.max_prove_attempts
