"""human_ref: the reference.v measurement event (bench "Verity vs human")."""

import pytest

from verity.contracts import CaptionEvent, HumanRefEvent, RunPhaseEvent, WinnerEvent, event_to_wire
from verity.core.pack import TaskPack
from verity.core.rtl import normalize_rtl_text
from verity.core.types import ToolInfraError
from verity.orchestrator import human_ref
from verity.runs.ledger import RunLedger
from verity_test_support import FakeToolRunner, execute_fake_run, make_settings

pytestmark = pytest.mark.asyncio


class RefProbeTools(FakeToolRunner):
    """Fails measure or EQY for the reference only, and records every Rtl it was asked to measure."""

    def __init__(self, *, fail_ref_measure: bool = False, fail_eqy: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.fail_ref_measure = fail_ref_measure
        self.fail_eqy = fail_eqy
        self.ref_text: str | None = None

    async def measure(self, rtl, pack):
        if self.ref_text is not None and rtl.text == self.ref_text and self.fail_ref_measure:
            raise ToolInfraError("measure sandbox died")
        return await super().measure(rtl, pack)

    async def check_equivalence(self, gold, gate, pack):
        if self.fail_eqy:
            raise ToolInfraError("eqy sandbox died")
        return await super().check_equivalence(gold, gate, pack)


def refs(events):
    return [e for e in events if isinstance(e, HumanRefEvent)]


def assert_order(events):
    ref = next(i for i, e in enumerate(events) if isinstance(e, HumanRefEvent))
    win = max(i for i, e in enumerate(events) if isinstance(e, WinnerEvent))
    term = next(i for i, e in enumerate(events) if isinstance(e, RunPhaseEvent) and e.phase == "done")
    assert win < ref < term


@pytest.mark.parametrize("mode", ["full", "fast"])
async def test_emitted_once_between_winner_and_terminal(settings, mac4: TaskPack, mode) -> None:
    tools = RefProbeTools()
    run = await execute_fake_run(settings, mac4, mode=mode, tools=tools)
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        found = refs(run.events)
        assert len(found) == 1
        assert_order(run.events)
        ev = found[0]
        assert ev.ppa.area_um2 is not None and ev.ppa.pdk == "Nangate45"
        assert ev.eqy.status == "proven" and ev.eqy.tool == "yosys-eqy"
        winner = [e for e in run.events if isinstance(e, WinnerEvent)][-1]
        assert ev.eqy.against_id == winner.candidate_id
        wire = event_to_wire(ev)
        assert set(wire["ppa"]) >= {"areaUm2", "cellCount", "clockPeriodNs", "powerMw", "tools", "pdk"}
        assert wire["t"] == "human_ref"
    finally:
        await run.store.close()


async def test_measure_failure_gives_null_metrics_and_run_still_done(settings, mac4: TaskPack) -> None:
    tools = RefProbeTools(fail_ref_measure=True)
    tools.ref_text = normalize_rtl_text(mac4.reference_rtl())
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools)
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        (ev,) = refs(run.events)
        assert ev.ppa.area_um2 is None and ev.ppa.cell_count is None and ev.ppa.tools == []
        assert_order(run.events)
    finally:
        await run.store.close()


async def test_eqy_infra_error_is_inconclusive(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast", tools=RefProbeTools(fail_eqy=True))
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        (ev,) = refs(run.events)
        assert ev.eqy.status == "inconclusive"
        assert ev.ppa.area_um2 is not None
    finally:
        await run.store.close()


async def test_pack_without_reference_emits_nothing(settings, mac4: TaskPack, monkeypatch) -> None:
    """The fake LLM needs reference.v to answer, so hide it only while the human_ref stage runs."""
    real_stage, real_ref = human_ref.measure_human_ref, TaskPack.reference_rtl

    async def hidden(st, winner_id):
        monkeypatch.setattr(TaskPack, "reference_rtl", lambda self: None)
        try:
            await real_stage(st, winner_id)
        finally:
            monkeypatch.setattr(TaskPack, "reference_rtl", real_ref)

    monkeypatch.setattr(human_ref, "measure_human_ref", hidden)
    run = await execute_fake_run(settings, mac4, mode="fast", tools=FakeToolRunner())
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        assert refs(run.events) == []
    finally:
        await run.store.close()


async def test_wall_budget_exhausted_skips_tools_and_keeps_status(settings, mac4: TaskPack, monkeypatch) -> None:
    real = RunLedger.exhausted
    armed = {"on": False}
    real_stage = human_ref.measure_human_ref

    async def arm(st, winner_id):
        armed["on"] = True
        before = (st.ctx.tools.measure_calls, st.ctx.tools.eqy_calls)
        await real_stage(st, winner_id)
        assert (st.ctx.tools.measure_calls, st.ctx.tools.eqy_calls) == before

    monkeypatch.setattr(human_ref, "measure_human_ref", arm)
    monkeypatch.setattr(RunLedger, "exhausted", lambda self: "max_wall_s" if armed["on"] else real(self))
    run = await execute_fake_run(settings, mac4, mode="fast", tools=FakeToolRunner())
    try:
        (ev,) = refs(run.events)
        assert ev.ppa.area_um2 is None and ev.eqy.status == "not_run"
        assert ev.eqy.log == "Not run: the time limit was reached."
        assert run.outcome is not None and run.outcome.status == "done"
    finally:
        await run.store.close()


async def test_wall_budget_hit_after_measure_skips_eqy(settings, mac4: TaskPack, monkeypatch) -> None:
    real = RunLedger.exhausted
    real_stage = human_ref.measure_human_ref
    state = {"armed": False, "tools": None}

    async def arm(st, winner_id):
        state["armed"], state["tools"] = True, st.ctx.tools
        await real_stage(st, winner_id)

    def exhausted(self):
        tools = state["tools"]
        if state["armed"] and tools.measure_calls > state["measure_at_entry"]:
            return "max_wall_s"
        return real(self)

    async def arm2(st, winner_id):
        state["measure_at_entry"] = st.ctx.tools.measure_calls
        state["eqy_at_entry"] = st.ctx.tools.eqy_calls
        await arm(st, winner_id)

    monkeypatch.setattr(human_ref, "measure_human_ref", arm2)
    monkeypatch.setattr(RunLedger, "exhausted", exhausted)
    run = await execute_fake_run(settings, mac4, mode="fast", tools=FakeToolRunner())
    try:
        (ev,) = refs(run.events)
        assert ev.ppa.area_um2 is not None, "measure ran"
        assert ev.eqy.status == "not_run" and ev.eqy.log == "Not run: the time limit was reached."
        assert run.tools.measure_calls == state["measure_at_entry"] + 1
        assert run.tools.eqy_calls == state["eqy_at_entry"], "EQY skipped"
        assert run.outcome is not None and run.outcome.status == "done"
    finally:
        await run.store.close()


async def test_end_budget_path_with_a_winner_emits_human_ref(settings, mac4: TaskPack, monkeypatch) -> None:
    from verity.core.types import BudgetExhausted
    from verity.orchestrator import evolve, machine

    async def boom(st, current):
        raise BudgetExhausted("max_llm_calls")

    calls = []
    real_end = machine._end_budget

    async def spy(st, limit):
        calls.append(limit)
        await real_end(st, limit)

    monkeypatch.setattr(evolve, "apply_winner_override", boom)
    monkeypatch.setattr(machine, "_end_budget", spy)
    run = await execute_fake_run(settings, mac4, mode="fast", tools=FakeToolRunner())
    try:
        assert calls == ["max_llm_calls"], "the _end_budget path ran"
        assert run.outcome is not None and run.outcome.status == "cap_reached" and run.outcome.winner_id
        assert len(refs(run.events)) == 1
        ref = next(i for i, e in enumerate(run.events) if isinstance(e, HumanRefEvent))
        term = next(i for i, e in enumerate(run.events) if isinstance(e, RunPhaseEvent) and e.phase == "cap_reached")
        assert ref < term
    finally:
        await run.store.close()


async def test_caption_precedes_the_measurement(settings, mac4: TaskPack) -> None:
    run = await execute_fake_run(settings, mac4, mode="fast", tools=FakeToolRunner())
    try:
        text = "Measuring the human-written design with the same tools."
        idx = [i for i, e in enumerate(run.events) if isinstance(e, CaptionEvent) and e.text == text]
        assert len(idx) == 1
        ref = next(i for i, e in enumerate(run.events) if isinstance(e, HumanRefEvent))
        assert idx[0] < ref
    finally:
        await run.store.close()


async def test_prove_timeout_skips_the_reference_eqy(settings, mac4: TaskPack) -> None:
    tools = FakeToolRunner(eqy_status="timeout")
    run = await execute_fake_run(settings, mac4, mode="full", tools=tools)
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        assert tools.eqy_calls >= 1, "the winner prove ran and timed out"
        (ev,) = refs(run.events)
        assert ev.eqy.status == "not_run"
        assert ev.eqy.log == "Not run: equivalence timed out on this design size."
        assert ev.ppa.area_um2 is not None, "the reference was still measured"
    finally:
        await run.store.close()
