"""The winner rule: a version can only win if it is no worse than v1 on the run's goal metric (zero tolerance).

The evidence is a real counter run (goal speed): v2 had clock 0.350 ns against v1's 0.344 ns, but its area and power
gains outweighed the slower clock in the blended score, so it was picked. Elites keep the plain ranking.
"""

from verity.contracts import PPA, Candidate
from verity.core.fitness import compute_fitness, pick_winner, result_key, select_elites, worse_on_goal


def ppa(area: float | None, clock: float | None, power: float | None = None, cells: int | None = 10) -> PPA:
    return PPA(area_um2=area, cell_count=cells, clock_period_ns=clock, power_mw=power, pdk="Nangate45")


def version(cid: str, p: PPA, base: PPA, priority: str, *, status: str = "measured") -> Candidate:
    measured = status == "measured"
    return Candidate(
        id=cid, label="v" + cid[1:], round=1, parent_id=None if cid == "c1" else "c1", created_by="INIT" if cid == "c1" else "OPTIMIZE",
        reason="r", rtl="", status=status, ppa=p,  # type: ignore[arg-type]
        fitness=compute_fitness(base, p, priority, "c1") if measured else None,  # type: ignore[arg-type]
        is_baseline=cid == "c1",
    )


def run(priority: str, base: PPA, *others: tuple[str, PPA]) -> list[Candidate]:
    return [version("c1", base, base, priority), *(version(cid, p, base, priority) for cid, p in others)]


def test_counter_run_speed_goal_keeps_v1_although_v2_scores_higher() -> None:
    base = ppa(61.98, 0.344, 0.0100)
    v2 = ppa(60.37, 0.350, 0.0090)  # area -2.6%, power -10%, clock +1.7%
    v4 = ppa(66.25, 0.396, 0.0110)  # +15% clock
    cands = run("speed", base, ("c2", v2), ("c4", v4))
    by_id = {c.id: c for c in cands}
    assert by_id["c2"].fitness.score > 0, "today's blended score prefers v2"  # type: ignore[union-attr]
    assert select_elites(cands, 2)[0] == "c2", "elites keep the plain ranking"
    assert pick_winner(cands) == "c1"
    assert worse_on_goal(by_id["c2"], by_id["c1"]) and worse_on_goal(by_id["c4"], by_id["c1"])
    assert not worse_on_goal(by_id["c1"], by_id["c1"])


def test_speed_goal_an_equal_clock_is_allowed_and_the_smaller_one_wins() -> None:
    base = ppa(100.0, 1.0, 0.010)
    cands = run("speed", base, ("c2", ppa(95.0, 1.0, 0.009)), ("c3", ppa(90.0, 1.001, 0.005)))
    assert pick_winner(cands) == "c2", "equal clock passes, 0.1% slower does not, however small the area"


def test_area_goal_a_bigger_area_never_wins_even_if_faster_and_lower_power() -> None:
    base = ppa(100.0, 1.0, 0.010)
    bigger = ppa(100.5, 0.5, 0.005)  # clock -50%, power -50%, area +0.5%
    cands = run("area", base, ("c2", bigger))
    assert cands[1].fitness.score > 0  # type: ignore[union-attr]
    assert pick_winner(cands) == "c1"
    smaller = ppa(80.0, 1.2, 0.011)  # smaller area passes although slower and hungrier
    assert pick_winner(run("area", base, ("c2", bigger), ("c3", smaller))) == "c3"


def test_power_goal_more_power_never_wins() -> None:
    base = ppa(100.0, 1.0, 0.010)
    hungry = ppa(60.0, 0.6, 0.0101)  # area -40%, clock -40%, power +1%
    cands = run("power", base, ("c2", hungry))
    assert cands[1].fitness.score > 0  # type: ignore[union-attr]
    assert pick_winner(cands) == "c1"
    assert pick_winner(run("power", base, ("c2", hungry), ("c3", ppa(90.0, 1.0, 0.010)))) == "c3", "equal power passes"


def test_a_missing_goal_metric_falls_back_to_the_plain_ranking() -> None:
    no_power_v1 = ppa(100.0, 1.0, None)
    v2 = ppa(50.0, 0.9, 0.5)
    cands = run("power", no_power_v1, ("c2", v2))
    assert pick_winner(cands) == "c2", "v1 has no power value, so there is nothing to compare"
    base = ppa(100.0, 1.0, 0.010)
    assert pick_winner(run("power", base, ("c2", ppa(50.0, 0.9, None)))) == "c2", "the version has no power value"


def test_a_copy_never_wins_over_the_original_and_not_after_the_original_is_demoted() -> None:
    base = ppa(100.0, 1.0)
    better = ppa(90.0, 0.9)
    cands = run("area", base, ("c2", better), ("c3", better))
    assert pick_winner(cands) == "c2", "the tie goes to the older version"
    assert pick_winner(cands, exclude=["c2"]) == "c1", "c3 has c2's result, so a failed proof of c2 rules it out too"
    other = ppa(95.0, 1.0)
    assert pick_winner(run("area", base, ("c2", better), ("c3", better), ("c4", other)), exclude=["c2"]) == "c4"


def test_v1_is_always_eligible_and_a_copy_of_v1_ties_back_to_v1() -> None:
    base = ppa(100.0, 1.0)
    cands = run("speed", base, ("c2", ppa(100.0, 1.0)))
    assert pick_winner(cands) == "c1"
    assert pick_winner(cands, exclude=["c2"]) == "c1"
    assert pick_winner([]) is None


def test_result_key_needs_cells_area_and_clock() -> None:
    assert result_key(ppa(1.0, 2.0, cells=3)) == (3, 1.0, 2.0)
    assert result_key(ppa(1.0, 2.0, cells=None)) is None
    assert result_key(ppa(None, 2.0)) is None
    assert result_key(None) is None

