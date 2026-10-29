"""Sign-off after the winner, spec/rules.json at the spec stage, and waveforms before repairs (CONTRACTS 6.3).

Fake provider and fake tool runner: no model, no sandbox. The real orchestrator, hub and store run end to end.
"""

import asyncio
import dataclasses
import json

import pytest

from verity.contracts import ArtifactEvent, CaptionEvent, RunPhaseEvent, WinnerEvent
from verity.contracts.signoff import Signoff, SpecRules, Waves
from verity.core.errors import ToolInfraError
from verity.core.pack import TaskPack
from verity.roles.repair import cycle_table
from verity_test_support import FakeToolRunner, execute_fake_run

pytestmark = pytest.mark.asyncio


def artifacts(events, prefix: str) -> list[ArtifactEvent]:
    return [e for e in events if isinstance(e, ArtifactEvent) and e.path.startswith(prefix)]


def the_signoff(events) -> Signoff:
    (found,) = artifacts(events, "signoff/")
    return Signoff.model_validate_json(found.content)


def winner_of(events) -> str:
    return [e for e in events if isinstance(e, WinnerEvent)][-1].candidate_id


def index_of(events, predicate) -> int:
    return next(i for i, e in enumerate(events) if predicate(e))


async def test_full_run_signs_off_the_winner_after_the_result(settings, mac4: TaskPack) -> None:
    tools = FakeToolRunner()
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools)
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        events = run.events
        winner = winner_of(events)
        report = the_signoff(events)
        assert report.candidate_id == winner

        # Order: run done, the Done caption, then the winner's waveform, the sign-off, the sign-off caption.
        done = index_of(events, lambda e: isinstance(e, RunPhaseEvent) and e.phase == "done")
        sign = index_of(events, lambda e: isinstance(e, ArtifactEvent) and e.path == f"signoff/{winner}.json")
        wave = max(i for i, e in enumerate(events) if isinstance(e, ArtifactEvent) and e.path == f"waves/{winner}.json")
        assert done < wave < sign, "the winner's sign-off waveform (an earlier one may come from its own repair)"
        last = events[-1]
        assert isinstance(last, CaptionEvent) and last.chapter == "Done"
        assert last.text.startswith("Sign-off for v") and "planted bugs caught" in last.text

        # Tests: seed 1 from the run plus the 8 extra seeds, all passing.
        assert [t.seed for t in report.tests.per_seed] == list(range(1, 10))
        assert report.tests.seeds_run == 9 and report.tests.seeds_passed == 9
        assert all(t.passed == t.total == mac4.test_count for t in report.tests.per_seed)
        # Lint by tag from the seeds call; the proofs from the prove stage and the human reference.
        assert report.lint.by_tag == {"UNUSEDSIGNAL": 1} and report.lint.warnings == 1 and report.lint.latches == 0
        assert report.eqy_vs_v1 in ("proven", "v1_is_winner")
        assert report.eqy_vs_human == "proven"
        # Strength: every planted bug caught by the fake tests.
        strength = report.strength
        assert strength is not None and strength.status == "ok"
        assert strength.planted == len(tools.mutants_seen) > 0 and strength.caught == strength.planted
        assert strength.harmless == strength.undecided == 0
        assert strength.note == "The tests caught every planted bug."
        assert tools.seeds_calls == 1 and tools.strength_calls == 1
        assert None in tools.wave_calls, "the winner's waveform is its first case"

        waves = Waves.model_validate_json(artifacts(events, f"waves/{winner}")[-1].content)
        assert waves.fail_cycle is None and waves.candidate_id == winner

        wire = json.loads(artifacts(events, "signoff/")[0].content)
        assert set(wire) == {"candidateId", "tests", "lint", "eqyVsV1", "eqyVsHuman", "strength", "durationMs"}
        assert set(wire["tests"]) == {"seedsRun", "seedsPassed", "perSeed"} and set(wire["lint"]) >= {"byTag", "latches"}

        stored = await run.store.read_run_file(run.run_id, "final/signoff.json")
        assert stored is not None and Signoff.model_validate_json(stored) == report
        table = await run.store.read_run_file(run.run_id, "final/signoff-mutants.json")
        assert table is not None and len(json.loads(table)) == len(tools.mutants_seen)
    finally:
        await run.store.close()


async def test_fast_run_reports_v1_as_the_winner(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast")
    try:
        report = the_signoff(run.events)
        assert report.candidate_id == "c1" and report.eqy_vs_v1 == "v1_is_winner"
        assert report.tests.seeds_run == 9
    finally:
        await run.store.close()


@pytest.mark.parametrize(
    ("eqy_status", "harmless", "undecided", "phrase"),
    [
        ("proven", 3, 1, "proven to behave exactly like the winner"),
        ("inconclusive", 0, 4, "3 could not be decided by the proof. 1 was not checked (the proof checks at most 3)."),
        ("not_equivalent", 0, 1, "proven different"),
    ],
)
async def test_surviving_bugs_are_checked_with_eqy(settings, mac4: TaskPack, eqy_status, harmless, undecided, phrase) -> None:
    tools = FakeToolRunner(eqy_status=eqy_status, survivors=4)
    run = await execute_fake_run(settings, mac4, mode="fast", tools=tools)
    try:
        strength = the_signoff(run.events).strength
        assert strength is not None and strength.status == "ok"
        assert strength.caught == strength.planted - 4
        assert strength.harmless == harmless and strength.undecided == undecided
        missed = strength.planted - strength.caught - strength.harmless - strength.undecided
        assert missed == (3 if eqy_status == "not_equivalent" else 0)
        assert phrase in (strength.note or ""), strength.note
        assert tools.eqy_calls == 1 + 3, "the human reference proof plus at most 3 survivor proofs"
    finally:
        await run.store.close()


async def test_sign_off_off_means_no_artifact(settings, mac4: TaskPack) -> None:
    off = settings.model_copy(update={"signoff": settings.signoff.model_copy(update={"enabled": False})})
    run = await execute_fake_run(off, mac4, mode="fast")
    try:
        assert not artifacts(run.events, "signoff/")
        assert run.events[-1].text.startswith("Done.")
    finally:
        await run.store.close()


async def test_no_time_left_skips_the_checks_but_still_reports(settings, mac4: TaskPack) -> None:
    tools = FakeToolRunner()
    none_left = settings.model_copy(update={"signoff": settings.signoff.model_copy(update={"max_s": 0})})
    run = await execute_fake_run(none_left, mac4, mode="fast", tools=tools)
    try:
        report = the_signoff(run.events)
        assert report.strength is not None and report.strength.status == "skipped"
        assert report.strength.note == "Not run: the time limit was reached."
        assert report.tests.seeds_run == 1, "only the seed the run already used"
        assert tools.seeds_calls == tools.strength_calls == 0
    finally:
        await run.store.close()


class BrokenSignoffTools(FakeToolRunner):
    async def verify_seeds(self, rtl, pack, seeds):
        raise ToolInfraError("seeds sandbox died")

    async def test_strength(self, rtl, pack, mutants):
        raise ToolInfraError("strength sandbox died")


async def test_sandbox_errors_never_fail_the_run(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast", tools=BrokenSignoffTools())
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        report = the_signoff(run.events)
        assert report.strength is not None and report.strength.status == "failed"
        assert report.tests.seeds_run == 1 and report.lint.errors == 0
    finally:
        await run.store.close()


class SlowStrengthTools(FakeToolRunner):
    async def test_strength(self, rtl, pack, mutants):
        await asyncio.sleep(30)
        return await super().test_strength(rtl, pack, mutants)


async def test_the_deadline_cuts_a_slow_part_and_reports_the_rest(settings, mac4: TaskPack) -> None:
    short = settings.model_copy(update={"signoff": settings.signoff.model_copy(update={"max_s": 1})})
    run = await execute_fake_run(short, mac4, mode="fast", tools=SlowStrengthTools())
    try:
        report = the_signoff(run.events)
        assert report.tests.seeds_run == 9, "the seeds finished in time"
        assert report.strength is not None and report.strength.status == "failed"
        assert report.strength.note == "Did not finish within the time limit."
    finally:
        await run.store.close()


async def test_spec_rules_artifact_lists_the_pack_rules_first(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast")
    try:
        (found,) = artifacts(run.events, "spec/rules.json")
        rules = SpecRules.model_validate_json(found.content).rules
        assert rules[: len(mac4.coding_rules)] == mac4.coding_rules
        rest = rules[len(mac4.coding_rules) :]
        assert any("rising edge" in r for r in rest) and any("active low and synchronous" in r for r in rest)
        assert any("Latency is 16 cycles" in r for r in rest) and any("lane i is a[i*8 +: 8]" in r for r in rest)
        spec_at = index_of(run.events, lambda e: getattr(e, "t", "") == "spec")
        assert run.events.index(found) == spec_at + 1
    finally:
        await run.store.close()


async def test_a_failing_version_gets_a_waveform_and_its_repair_reads_it(settings, mac4: TaskPack) -> None:
    seen: list[dict] = []

    def hook(ctx) -> None:
        roles = ctx.roles

        async def repair(*a, **k):
            seen.append(k)
            return await roles.repair(*a, **k)

        ctx.roles = dataclasses.replace(roles, repair=repair)

    tools = FakeToolRunner()
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools, on_ctx=hook)
    try:
        assert seen, "the fake EXPLORE child fails once and is repaired"
        trace = seen[0].get("waves")
        assert trace is not None and trace.fail_cycle == 3
        table = cycle_table(trace)
        assert "cycle" in table and "3*" in table and "q expected" in table
        assert "acc_case" in tools.wave_calls, "the waveform is of the first failing case"
        child_waves = [e for e in artifacts(run.events, "waves/") if e.path != f"waves/{winner_of(run.events)}.json"]
        assert child_waves and Waves.model_validate_json(child_waves[0].content).fail_cycle == 3
    finally:
        await run.store.close()


async def test_no_waveform_still_repairs(settings, mac4: TaskPack) -> None:
    seen: list[dict] = []

    def hook(ctx) -> None:
        roles = ctx.roles

        async def repair(*a, **k):
            seen.append(k)
            return await roles.repair(*a, **k)

        ctx.roles = dataclasses.replace(roles, repair=repair)

    run = await execute_fake_run(settings, mac4, mode="full", tools=FakeToolRunner(waves=False), on_ctx=hook)
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        assert seen and all("waves" not in k for k in seen)
    finally:
        await run.store.close()
