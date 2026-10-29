"""W6: research per block, evidence in the view, improve rounds. All with fakes, no network, $0."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_cpu_jobs import ScriptedRuns, finish, setup

from verity.config import CpuJobConfig
from verity.contracts.cpu import CpuEvidence, CreateCpuJobRequest
from verity.core.context import RunOutcome
from verity.core.records import RunOutcomeRow, StrategyOutcomeRow
from verity.research.service import RunReferences
from verity.runs.cpu_job import BLOCKS, Measure, is_better, pick_block, version_totals
from verity.runs.manager import RunManager

BLOCK_IDS = [p for p, _ in BLOCKS]


def req(goal="area"):
    return CreateCpuJobRequest(specId="rv32i_micro", goal=goal)


def calls_for(runs, pack_id):
    return [c for c in runs.calls if c["preset_id"] == pack_id]


class FakeResearch:
    """Stands in for ResearchService: returns a saved research per block, or fails on request."""

    def __init__(self, usd=0.0, state="ran"):
        self.usd, self.state = usd, state
        self.broken = set()
        self.skipped = set()
        self.requests = []

    def worst_case_usd(self):
        return 0.0

    async def lookup_or_search(self, request, *, session_id):
        self.requests.append((request.family, request.intent_class, session_id))
        if request.family in self.broken:
            raise RuntimeError("search is down")
        if request.family in self.skipped:
            return SimpleNamespace(state="skipped", research=None, why="credits")
        research = SimpleNamespace(id=f"rs_{request.family}", usd=self.usd)
        return SimpleNamespace(state=self.state, research=research, why="")


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def _wins(**kw):
    return {b: Measure(area_um2=kw.get("area", {}).get(b, 1.0), clock_ns=kw.get("clock", {}).get(b, 1.0),
                       power_mw=kw.get("power", {}).get(b, 1.0)) for b in BLOCK_IDS}


def test_pick_block_per_goal():
    wins = _wins(area={"rv32i_rf": 90.0}, clock={"rv32i_br": 7.5}, power={"rv32i_lsu": 3.0})
    assert pick_block("area", wins) == "rv32i_rf"
    assert pick_block("speed", wins) == "rv32i_br"
    assert pick_block("power", wins) == "rv32i_lsu"
    # Ties go to the earlier block in contract order; a block with no number is never picked.
    assert pick_block("area", _wins()) == "rv32i_imm"
    flat = _wins()
    flat["rv32i_imm"] = Measure()
    assert pick_block("area", flat) == "rv32i_dec"
    assert pick_block("power", {b: Measure(area_um2=5.0) for b in BLOCK_IDS}) is None


def test_is_better_is_strict_and_needs_both_numbers():
    old = Measure(area_um2=10.0, clock_ns=2.0)
    assert is_better("area", Measure(area_um2=9.9), old)
    assert not is_better("area", Measure(area_um2=10.0), old)
    assert not is_better("area", Measure(area_um2=11.0), old)
    assert not is_better("area", Measure(), old)
    assert not is_better("speed", Measure(clock_ns=1.0), Measure())
    assert not is_better("power", Measure(area_um2=1.0), Measure(area_um2=2.0))


def test_version_totals_sum_area_and_power_and_use_the_longest_clock():
    wins = {b: Measure(area_um2=10.0, clock_ns=1.0 + i, power_mw=0.5) for i, b in enumerate(BLOCK_IDS)}
    totals = version_totals(wins)
    assert totals.area_um2 == 70.0 and totals.clock_ns == 7.0 and totals.power_mw == 3.5
    wins["rv32i_alu"] = Measure(area_um2=10.0, clock_ns=None, power_mw=0.5)
    totals = version_totals(wins)
    assert totals.area_um2 == 70.0 and totals.clock_ns is None and totals.power_mw == 3.5
    assert version_totals({}).area_um2 is None


# ---------------------------------------------------------------------------
# research per block
# ---------------------------------------------------------------------------
async def test_research_id_is_passed_per_block_and_spend_counts(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    runs._research = FakeResearch(usd=0.05)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req("speed"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed"
        for block_id in BLOCK_IDS:
            (call,) = calls_for(runs, block_id)
            assert call["research_id"] == f"rs_{block_id}" and call["mode"] == "full"
        assert runs.calls[-1]["research_id"] is None  # the core has no research
        assert [b.research for b in view.blocks] == ["ran"] * 7
        assert [b.research_id for b in view.blocks] == [f"rs_{b}" for b in BLOCK_IDS]
        assert sorted(f for f, _, _ in runs._research.requests) == sorted(BLOCK_IDS)
        assert {c for _, c, _ in runs._research.requests} == {"in_family"}
        assert {s for _, _, s in runs._research.requests} == {"owner"}
        # 8 runs at 0.1 plus 7 researches at 0.05 count toward the job.
        assert view.spent_usd == pytest.approx(0.8 + 7 * 0.05)
    finally:
        await store.close()


async def test_research_spend_counts_against_the_job_cap(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(max_usd=0.9))
    runs._research = FakeResearch(usd=0.05)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        # 7 researches (0.35) leave 0.55 for runs, and the 8 runs would cost 0.8: the job cannot finish.
        assert view.status == "failed"
        assert view.spent_usd <= 0.9 + 1e-9
        assert len(runs.calls) < 8
    finally:
        await store.close()


async def test_research_failure_still_runs_the_block_and_says_so(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    runs._research = FakeResearch()
    runs._research.broken.add("rv32i_alu")
    runs._research.skipped.add("rv32i_br")
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and all(b.status == "passed" for b in view.blocks)
        by_id = {b.block_id: b for b in view.blocks}
        assert by_id["rv32i_alu"].research == "failed" and by_id["rv32i_alu"].research_id is None
        assert by_id["rv32i_br"].research == "skipped" and by_id["rv32i_br"].research_id is None
        assert calls_for(runs, "rv32i_alu")[0]["research_id"] is None
        assert calls_for(runs, "rv32i_br")[0]["research_id"] is None
        assert calls_for(runs, "rv32i_imm")[0]["research_id"] == "rs_rv32i_imm"
    finally:
        await store.close()


async def test_no_research_service_means_off(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and [b.research for b in view.blocks] == ["off"] * 7
        assert all(c["research_id"] is None for c in runs.calls)
    finally:
        await store.close()


async def test_start_cpu_run_passes_research_through_the_chip_run_checks(tmp_path, cpu_packs, monkeypatch):
    """The real RunManager path: research_id reaches references_for_run (ownership and family checks live there),
    skip_techniques drops tried ids, and candidate_multiplier widens this run only."""
    _, store, runs = setup(tmp_path, cpu_packs, cls=RunManager)
    seen = []

    class Refs:
        async def references_for_run(self, **kw):
            seen.append(kw)
            return RunReferences(key="k", research_id=kw["research_id"], techniques=["a", "b", "c"])

        def worst_case_usd(self):
            return 0.0

    runs._research = Refs()
    ctxs = []

    async def drive(ctx, **kwargs):
        ctxs.append(ctx)
        ctx.outcome = RunOutcome(status="done", winner_id="c1")

    monkeypatch.setattr("verity.runs.manager.run_orchestrator", drive)
    await store.open()
    try:
        plain = await runs.start_cpu_run(
            session_id="owner", preset_id="rv32i_alu", priority="area", mode="full", max_usd=0.5, research_id="rs_x")
        wide = await runs.start_cpu_run(
            session_id="owner", preset_id="rv32i_alu", priority="area", mode="full", max_usd=0.5,
            research_id="rs_x", candidate_multiplier=2, skip_techniques=["b"])
        for run in (plain, wide):
            await runs.wait_for_run(run.id)
        assert [s["research_id"] for s in seen] == ["rs_x", "rs_x"]
        assert all(s["family"] == "rv32i_alu" and s["session_id"] == "owner" for s in seen)
        by_run = {c.run_id: c for c in ctxs}
        assert by_run[plain.id].references.techniques == ["a", "b", "c"]
        assert by_run[wide.id].references.techniques == ["a", "c"]
        base = runs._settings.evolution
        assert by_run[plain.id].ledger.limits.max_candidates == runs._settings.budget.run.max_candidates
        assert by_run[wide.id].ledger.limits.max_candidates == 2 * runs._settings.budget.run.max_candidates
        wide_ev = by_run[wide.id].settings.evolution
        assert wide_ev.round1_children == 2 * base.round1_children and len(wide_ev.round1_strategies) == 2 * len(base.round1_strategies)
        assert wide_ev.round2_children == 2 * base.round2_children
        assert by_run[plain.id].settings.evolution.round1_children == base.round1_children
        assert runs._settings.evolution.round1_children == base.round1_children
    finally:
        await runs.shutdown()
        await store.close()


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
async def test_evidence_is_read_from_the_signoff_sheet_and_run_record(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    runs.repairs["rv32i_dec"] = 2
    runs.eqys["rv32i_dec"] = ["inconclusive"]

    async def record_techniques(run_id, run):
        if run.preset_id == "rv32i_dec":  # an unproven winner falls back to a passing v1, so give it one
            await store.write_run_file(run_id, "candidates/c1/attempt-1.v", b"module rv32i_dec; endmodule\n", "text/x-verilog")
            await store.write_run_file(run_id, "candidates/c1/attempt-1.verify.json", b'{"status": "pass"}', "application/json")
            await store.upsert_candidate(run_id, (await _baseline_row(store, run_id)))
        if run.preset_id != "rv32i_alu":
            return
        common = dict(run_id=run_id, key="key-alu", pack_sha256=run.task_pack_sha256, tool_image_digest="img")
        await store.put_run_outcome(RunOutcomeRow(
            family="rv32i_alu", priority="area", mode="full", status="done", partial=False,
            created_at=datetime.now(UTC), **common))
        await store.put_strategy_outcomes(run_id, [
            StrategyOutcomeRow(candidate_id="c2", label="v2", strategy="EXPLORE", technique="barrel_shifter", result="improved", **common),
            StrategyOutcomeRow(candidate_id="c3", label="v3", strategy="OPTIMIZE", technique=None, result="not_better", **common),
            StrategyOutcomeRow(candidate_id="c4", label="v4", strategy="EXPLORE", technique="shared_adder", result="improved", **common),
        ])

    runs.after_run = record_techniques
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        by_id = {b.block_id: b for b in view.blocks}
        dec = by_id["rv32i_dec"].evidence
        assert dec.eqy_vs_v1 == "inconclusive" and dec.repairs == 2
        assert by_id["rv32i_dec"].used_version == "v1"
        assert (dec.tests_passed, dec.tests_total) == (20, 20)
        assert (dec.seeds_passed, dec.seeds_run) == (3, 3)
        assert (dec.mutants_killed, dec.mutants_total) == (9, 10)
        assert dec.techniques is None  # no outcome row for this run: not available, never guessed
        assert by_id["rv32i_alu"].evidence.techniques == ["barrel_shifter", "shared_adder"]
        assert by_id["rv32i_imm"].evidence.eqy_vs_v1 == "proven" and by_id["rv32i_imm"].evidence.repairs == 0
        assert view.core.evidence.eqy_vs_v1 == "proven" and view.core.evidence.seeds_run == 3
    finally:
        await store.close()


async def test_evidence_is_null_when_the_sheet_is_missing_or_for_another_version(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)

    runs.sheet_modes = {"rv32i_imm": "garbage", "rv32i_br": "other_version"}

    async def passing_v1(run_id, run):
        if run.preset_id in runs.sheet_modes:  # no sheet means unproven, so the block falls back to a passing v1
            await store.write_run_file(run_id, "candidates/c1/attempt-1.v", f"module {run.preset_id}; endmodule\n".encode(), "text/x-verilog")
            await store.write_run_file(run_id, "candidates/c1/attempt-1.verify.json", b'{"status": "pass"}', "application/json")
            await store.upsert_candidate(run_id, (await _baseline_row(store, run_id)))

    runs.after_run = passing_v1
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        by_id = {b.block_id: b for b in view.blocks}
        for block_id in ("rv32i_imm", "rv32i_br"):
            ev = by_id[block_id].evidence
            assert ev == CpuEvidence(repairs=0), block_id
            assert by_id[block_id].used_version == "v1", block_id
        assert view.status == "passed"
        assert by_id["rv32i_alu"].evidence.eqy_vs_v1 == "proven"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# improve rounds
# ---------------------------------------------------------------------------
def area_runs(tmp_path, cpu_packs, alu_areas, **updates):
    """Seven blocks of area 10 except the alu, whose k-th run reports alu_areas[k]. Winner text is tagged by run."""
    _, store, runs = setup(tmp_path, cpu_packs, **updates)
    for block_id in BLOCK_IDS:
        runs.ppas[block_id] = [(10.0, 2.0, 1.0)]
    runs.ppas["rv32i_alu"] = [(a, 2.0, 1.0) for a in alu_areas]
    runs.tag_winners = True
    return store, runs


async def test_kept_improvement_reruns_the_core_as_v2_and_round_two_can_stop(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 30.0, 40.0])
    runs._research = FakeResearch()

    async def tried(run_id, run):
        if run.preset_id == "rv32i_alu":
            n = len(calls_for(runs, "rv32i_alu"))
            common = dict(run_id=run_id, key="key-alu", pack_sha256=run.task_pack_sha256, tool_image_digest="img")
            await store.put_run_outcome(RunOutcomeRow(
                family="rv32i_alu", priority="area", mode="full", status="done", partial=False,
                created_at=datetime.now(UTC), **common))
            await store.put_strategy_outcomes(run_id, [StrategyOutcomeRow(
                candidate_id="c2", label="v2", strategy="EXPLORE", technique=["t_first", "t_second", "t_third"][n - 1],
                result="improved", **common)])

    runs.after_run = tried
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req("area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and view.finished_at
        alu = calls_for(runs, "rv32i_alu")
        assert len(alu) == 3  # the first run, the round 1 improve run, the round 2 improve run
        assert alu[0]["mode"] == "full" and "candidate_multiplier" not in alu[0]
        assert alu[1]["candidate_multiplier"] == 2 and alu[1]["mode"] == "full"
        assert alu[1]["research_id"] == "rs_rv32i_alu"
        assert alu[1]["skip_techniques"] == ["t_first"]
        assert alu[2]["skip_techniques"] == ["t_first", "t_second"]
        # Only the alu was ever improved: it holds the largest area.
        assert [c["preset_id"] for c in runs.calls[8:]] == ["rv32i_alu", runs.calls[9]["preset_id"], "rv32i_alu"]
        core_v2 = runs.calls[9]
        assert core_v2["preset_id"].endswith("_v2") and core_v2["mode"] == "fast"
        assert core_v2["research_id"] is None
        # Round 2 found 40 against the kept 30: not kept, so no core v3.
        assert len(runs.calls) == 11
        assert [v.version for v in view.versions] == [1, 2]
        v1, v2 = view.versions
        assert v1.changed_block is None and v1.block_run_id is None and v1.core_run_id == view.core.run_id
        assert v2.changed_block == "rv32i_alu" and v2.block_run_id == "r_9" and v2.core_run_id == "r_10"
        assert v1.totals.area_um2 == 110.0 and v2.totals.area_um2 == 90.0
        assert v1.totals.clock_ns == 2.0 and v1.totals.power_mw == 7.0
        assert v2.block_evidence.eqy_vs_v1 == "proven"
        assert v2.core_summary.id == "r_10" and v1.core_summary.id == view.core.run_id
        # The v2 pack carries the improved alu (run r_9) and the other six first winners.
        pack = runs._packs[core_v2["preset_id"]]
        pack.reverify()
        text = (pack.root / "blocks.v").read_text()
        assert "module rv32i_alu; endmodule // r_9" in text and "module rv32i_alu; endmodule // r_3" not in text
        assert text.count("module rv32i_alu;") == 1 and "module rv32i_imm; endmodule // r_1" in text
        assert (pack.root / "reference.v").exists()
        v1_pack = runs._packs[view.core.pack_id]
        assert v1_pack.id != pack.id and "// r_9" not in (v1_pack.root / "blocks.v").read_text()
        # The block rows still show the first version of each block.
        assert next(b for b in view.blocks if b.block_id == "rv32i_alu").summary.id == "r_3"
        assert view.spent_usd == pytest.approx(1.1)
    finally:
        await store.close()


@pytest.mark.parametrize("goal,expected", [("area", "rv32i_rf"), ("speed", "rv32i_br"), ("power", "rv32i_lsu")])
async def test_improve_picks_the_block_with_the_largest_share_on_the_goal(tmp_path, cpu_packs, goal, expected):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=1))
    for block_id in BLOCK_IDS:
        runs.ppas[block_id] = [(10.0, 2.0, 1.0)]
    runs.ppas["rv32i_rf"] = [(90.0, 2.0, 1.0)]
    runs.ppas["rv32i_br"] = [(10.0, 7.5, 1.0)]
    runs.ppas["rv32i_lsu"] = [(10.0, 2.0, 3.0)]
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(goal), session_id="owner", role="team")
        await finish(runs, view.id)
        assert runs.calls[8]["preset_id"] == expected and runs.calls[8]["candidate_multiplier"] == 2
        assert runs.calls[8]["priority"] == goal
    finally:
        await store.close()


@pytest.mark.parametrize("second_run,eqy,why", [
    ((60.0, 2.0, 1.0), "proven", "worse"),
    ((50.0, 2.0, 1.0), "proven", "equal is not better"),
    ((20.0, 2.0, 1.0), "inconclusive", "unproven"),
    ((20.0, 2.0, 1.0), "not_equivalent", "not equivalent"),
    ((20.0, 2.0, 1.0), "not_run", "not run"),
    ((20.0, 2.0, 1.0), "v1_is_winner", "winner is its own v1"),
    (None, "proven", "no measurement"),
])
async def test_worse_or_unproven_new_winner_is_not_kept(tmp_path, cpu_packs, second_run, eqy, why):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0])
    runs.ppas["rv32i_alu"] = [(50.0, 2.0, 1.0)] + ([second_run] if second_run else [None])
    runs.eqys["rv32i_alu"] = ["proven", eqy]
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed", why
        assert len(calls_for(runs, "rv32i_alu")) == 2, why
        assert len(runs.calls) == 9, why  # 7 blocks, core, one improve run, and no core v2
        assert [v.version for v in view.versions] == [1], why
        assert view.message and "rv32i_alu" in view.message, why
        assert not any(c["preset_id"].endswith("_v2") for c in runs.calls), why
    finally:
        await store.close()


async def test_improve_run_that_fails_keeps_cpu_v1(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 20.0])
    await store.open()
    try:
        runs.failures["rv32i_alu"] = 0
        original = runs.start_cpu_run

        async def fail_second_alu(**kwargs):
            if kwargs["preset_id"] == "rv32i_alu" and kwargs.get("candidate_multiplier") == 2:
                runs.failures["rv32i_alu"] = 1
            return await original(**kwargs)

        runs.start_cpu_run = fail_second_alu
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and [v.version for v in view.versions] == [1]
        assert view.core.status == "passed" and view.message
    finally:
        await store.close()


async def test_core_v2_failure_drops_the_change_and_stops(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 20.0, 10.0])
    original = runs.start_cpu_run

    async def fail_core_v2(**kwargs):
        if kwargs["preset_id"].endswith("_v2"):
            runs.failures[kwargs["preset_id"]] = 1
        return await original(**kwargs)

    runs.start_cpu_run = fail_core_v2
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and [v.version for v in view.versions] == [1]
        assert "dropped" in view.message
        assert len(runs.calls) == 10  # 8, the improve block run, the failed core v2; nothing after
    finally:
        await store.close()


async def test_improve_stops_at_two_rounds(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 30.0, 20.0, 5.0])
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed"
        assert [v.version for v in view.versions] == [1, 2, 3]
        assert len(calls_for(runs, "rv32i_alu")) == 3  # first run plus exactly two improve runs
        assert len(runs.calls) == 12  # 8 + (block, core v2) + (block, core v3)
        assert [v.totals.area_um2 for v in view.versions] == [110.0, 90.0, 80.0]
        assert runs.calls[-1]["preset_id"].endswith("_v3")
    finally:
        await store.close()


async def test_improve_rounds_config_zero_turns_it_off(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 20.0], cpu_job=CpuJobConfig(improve_rounds=0))
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and len(runs.calls) == 8 and [v.version for v in view.versions] == [1]
    finally:
        await store.close()


async def test_improve_stops_when_the_cap_cannot_hold_a_block_run_and_a_core_rerun(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 20.0], cpu_job=CpuJobConfig(max_usd=1.5))
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and len(runs.calls) == 8  # 0.7 left is less than two run caps
        assert "spend cap" in view.message
        assert view.spent_usd <= 1.5 + 1e-9
    finally:
        await store.close()


async def test_improve_stops_at_the_cap_after_a_first_round(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 30.0, 20.0], cpu_job=CpuJobConfig(max_usd=1.9))
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and [v.version for v in view.versions] == [1, 2]
        assert len(runs.calls) == 10  # round 2 would need 1.0 and only 0.9 is left
        assert "spend cap" in view.message
        assert view.spent_usd <= 1.9 + 1e-9
        assert sum(c["max_usd"] for c in runs.calls[8:]) <= 1.1 + 1e-9
    finally:
        await store.close()


async def test_no_measurement_means_no_improve_round(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req("power"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and len(runs.calls) == 8
        assert "no block reported its power" in view.message
        assert view.versions[0].totals.area_um2 is None
    finally:
        await store.close()


async def test_job_shows_improving_while_the_rounds_run(tmp_path, cpu_packs):
    store, runs = area_runs(tmp_path, cpu_packs, [50.0, 20.0])
    seen = []
    original = runs.start_cpu_run

    async def watch(**kwargs):
        if kwargs.get("candidate_multiplier") == 2:
            job = await store.get_cpu_job(next(iter(runs.cpu_jobs._tasks)))
            seen.append((job.view.status, [v.version for v in job.view.versions]))
        return await original(**kwargs)

    runs.start_cpu_run = watch
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        await finish(runs, view.id)
        # Round 1 starts with CPU v1 only; round 2 starts after v2 was kept.
        assert seen == [("improving", [1]), ("improving", [1, 2])]
    finally:
        await store.close()


async def test_restart_stops_an_improving_job(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        await finish(runs, view.id)
        job = await store.get_cpu_job(view.id)
        job.view.status, job.view.finished_at = "improving", None
        await store.save_cpu_job(job)
        runs2 = ScriptedRuns(settings, store, cpu_packs)
        await runs2.cpu_jobs.start()
        again = await runs2.cpu_jobs.get(view.id, session_id="owner", role="user")
        assert again.status == "stopped" and again.message == "server restarted" and again.finished_at
    finally:
        await store.close()


async def _baseline_row(store, run_id):
    """The fixture's winner c1 doubles as the run's baseline (v1) for the fallback path."""
    from verity.contracts import Candidate

    return Candidate(id="c1", label="v1", round=0, created_by="INIT", reason="fixture", rtl="", status="pass", is_baseline=True)
