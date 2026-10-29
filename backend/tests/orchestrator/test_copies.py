"""Copies (a new version that synthesizes to the same cells, area and clock as an earlier one) and the winner rule in a
full fake run, plus the "already tried" list handed to the optimizer."""

from types import SimpleNamespace

import pytest

from verity.contracts import PPA, CandidateEvent, WinnerEvent
from verity.core.pack import TaskPack
from verity.core.results import MeasureResult, SynthResult, TimingResult
from verity.orchestrator.candidates import Cand
from verity.orchestrator.evolve import _mark_copies, _tried
from verity_test_support import FakeToolRunner, execute_fake_run

pytestmark = pytest.mark.asyncio


def ppa(area: float | None, clock: float | None, cells: int | None = 100) -> PPA:
    return PPA(area_um2=area, cell_count=cells, clock_period_ns=clock, pdk="Nangate45")


def cand(n: int, p: PPA | None, *, status: str = "measured", parent: str | None = "c1", by: str = "OPTIMIZE") -> Cand:
    c = Cand(id=f"c{n}", label=f"v{n}", round=1, parent_id=parent, created_by=by)  # type: ignore[arg-type]
    c.ppa = p
    c.status = status  # type: ignore[assignment]
    c.reason = f"{by.title()} version of v1"
    return c


class Recorder:
    def __init__(self) -> None:
        self.emitted: list[str] = []

    async def candidate(self, c: Cand) -> None:
        self.emitted.append(c.id)


def state(*cands: Cand) -> SimpleNamespace:
    return SimpleNamespace(cands={c.id: c for c in cands}, em=Recorder())


async def test_the_older_version_is_the_original_whatever_order_they_finished_in() -> None:
    same = ppa(90.0, 1.0)
    st = state(cand(1, ppa(100.0, 1.2), parent=None, by="INIT"), cand(4, same), cand(3, same), cand(2, ppa(95.0, 1.1)))
    await _mark_copies(st)  # type: ignore[arg-type]
    assert st.cands["c4"].copy_of == "c3" and st.cands["c4"].reason == "Same result as v3"
    assert st.cands["c3"].copy_of is None and st.cands["c2"].copy_of is None and st.cands["c1"].copy_of is None
    assert st.cands["c4"].status == "measured", "a copy keeps the existing status; only its reason says copy"
    assert st.em.emitted == ["c4"]
    await _mark_copies(st)  # type: ignore[arg-type]
    assert st.em.emitted == ["c4"], "running it again changes nothing"


async def test_a_version_equal_to_v1_is_a_copy_of_v1() -> None:
    base = ppa(100.0, 1.2)
    st = state(cand(1, base, parent=None, by="INIT"), cand(2, base))
    await _mark_copies(st)  # type: ignore[arg-type]
    assert st.cands["c2"].copy_of == "c1" and st.cands["c2"].reason == "Same result as v1"


async def test_only_cells_area_and_clock_all_equal_make_a_copy() -> None:
    st = state(
        cand(1, ppa(100.0, 1.2), parent=None, by="INIT"),
        cand(2, ppa(90.0, 1.0)),
        cand(3, ppa(90.0, 1.0, cells=99)),  # other cell count
        cand(4, ppa(90.0, 1.1)),  # other clock
        cand(5, ppa(90.5, 1.0)),  # other area
        cand(6, ppa(90.0, None)),  # incomplete measurement is never called a copy
        cand(7, ppa(90.0, None)),
        cand(8, ppa(90.0, 1.0), status="rejected"),  # rejected versions are not measured results
    )
    await _mark_copies(st)  # type: ignore[arg-type]
    assert [c.copy_of for c in st.cands.values()] == [None] * 8


async def test_tried_lists_finished_siblings_only_not_v1_the_parent_or_the_child() -> None:
    v1 = cand(1, ppa(100.0, 1.0), parent=None, by="INIT")
    v2 = cand(2, ppa(98.0, 1.01), by="SIMPLIFY")
    v2.reason = "Dropped a redundant register"
    v3 = cand(3, None, status="rejected", by="EXPLORE")
    v4 = cand(4, None, status="simulating", by="OPTIMIZE")  # still in flight
    v5 = cand(5, ppa(97.0, 0.99), by="OPTIMIZE")  # the parent
    v6 = cand(6, None, status="drafting", by="OPTIMIZE")  # the child itself
    st = state(v1, v2, v3, v4, v5, v6)
    tried = _tried(st, v6, v5)  # type: ignore[arg-type]
    assert [(t.label, t.strategy, t.ppa is None) for t in tried] == [("v2", "SIMPLIFY", False), ("v3", "EXPLORE", True)]
    assert tried[0].reason == "Dropped a redundant register"


# ---------- a full fake run ----------
class ScriptedMeasure(FakeToolRunner):
    """Measure results by call order: call 1 is v1, the next calls follow `results` (the last one repeats)."""

    def __init__(self, results: list[tuple[float, float]]) -> None:
        super().__init__()
        self.results = results

    async def measure(self, rtl, pack):  # type: ignore[no-untyped-def]
        self.measure_calls += 1
        area, clock = self.results[min(self.measure_calls, len(self.results)) - 1]
        return MeasureResult(
            status="ok",
            synth=SynthResult(ok=True, area_um2=area, cell_count=100, cells={"NAND2_X1": 100}, tool="fake yosys"),
            sta=TimingResult(
                ok=True, clock_period_ns=clock, wns_ns=pack.target_clock_ns - clock, target_period_ns=pack.target_clock_ns, tool="fake sta"
            ),
            duration_ms=9,
        )


async def test_run_with_goal_speed_never_picks_a_slower_version_or_a_copy(settings, mac4: TaskPack) -> None:
    # v1 3.0 ns. Two children come out half the area but 13% slower (and are identical), one is a little faster.
    # The slow pair outscores the fast one in the blended speed score, so only the goal rule keeps them out.
    tools = ScriptedMeasure([(1000.0, 3.0), (500.0, 3.4), (500.0, 3.4), (990.0, 2.9), (990.0, 2.9)])
    run = await execute_fake_run(settings, mac4, mode="full", priority="speed", tools=tools)
    try:
        assert run.error is None, run.error
        final = {}
        for e in run.events:
            if isinstance(e, CandidateEvent):
                final[e.candidate.id] = e.candidate
        slow = [c for c in final.values() if c.ppa is not None and c.ppa.clock_period_ns == 3.4]
        fast = [c for c in final.values() if c.ppa is not None and c.ppa.clock_period_ns == 2.9]
        assert len(slow) == 2 and fast, "the scripted results reached the versions"
        assert all(c.fitness is not None and c.fitness.score > fast[0].fitness.score for c in slow)  # type: ignore[union-attr]

        copies = [c for c in final.values() if c.reason.startswith("Same result as")]
        slow_copy = max(slow, key=lambda c: int(c.id[1:]))
        assert slow_copy in copies and slow_copy.reason == f"Same result as {min(slow, key=lambda c: int(c.id[1:])).label}"
        assert all(c.status == "measured" for c in copies)

        winner_event = [e for e in run.events if isinstance(e, WinnerEvent)][-1]
        winner = final[winner_event.candidate_id]
        original_fast = min(fast, key=lambda c: int(c.id[1:]))
        assert winner.id == original_fast.id, "the fastest eligible original wins, not a slower or a copied version"
        assert winner.ppa is not None and winner.ppa.clock_period_ns == 2.9
        assert tools.eqy_calls >= 1
    finally:
        await run.store.close()
