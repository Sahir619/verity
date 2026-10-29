"""Fitness formula (frontend ARCHITECTURE 8.3, CONTRACTS section 7) against a recorded case from golden.json.

golden.json has all three metrics, so its recorded weights are the base weights. A candidate with power = null drops
that metric and renormalizes (CONTRACTS S8); the web script `validate-fixture.ts` implements the same recompute().
"""

import json

import pytest

from verity.contracts import PPA, Candidate, Fitness
from verity.core.fitness import BASE_WEIGHTS, compute_fitness, pick_winner, rankable, select_elites
from verity_test_support import GOLDEN_JSON


def web_recompute(base: PPA, c: PPA, priority: str) -> float:
    """Same algorithm as frontend/scripts/validate-fixture.ts recompute()."""
    w = BASE_WEIGHTS[priority]  # type: ignore[index]
    parts: list[tuple[float, float]] = []
    for weight, b, v in (
        (w["area"], base.area_um2, c.area_um2),
        (w["timing"], base.clock_period_ns, c.clock_period_ns),
        (w["power"], base.power_mw, c.power_mw),
    ):
        if b is None or v is None:
            continue
        parts.append((weight, 100 * (b - v) / b))
    total = sum(p[0] for p in parts)
    return 0.0 if total == 0 else sum(p[0] / total * p[1] for p in parts)


def ppa(area: float | None, clock: float | None, power: float | None = None) -> PPA:
    return PPA(area_um2=area, clock_period_ns=clock, power_mw=power, pdk="Nangate45")


def candidate(cid: str, area: float, clock: float, *, base: PPA, status: str = "measured", priority: str = "area") -> Candidate:
    p = ppa(area, clock)
    return Candidate(
        id=cid, label="v" + cid[1:], round=1, parent_id="c1", created_by="OPTIMIZE", reason="r", rtl="", status=status,  # type: ignore[arg-type]
        ppa=p, fitness=compute_fitness(base, p, priority, "c1") if status == "measured" else None,  # type: ignore[arg-type]
    )


@pytest.mark.skipif(not GOLDEN_JSON.is_file(), reason="golden.json not found")
def test_matches_the_recorded_golden_cases() -> None:
    log = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    final: dict[str, dict] = {}
    for event in log["events"]:
        if event["t"] == "candidate":
            final[event["candidate"]["id"]] = event["candidate"]
    base = PPA.model_validate(final["c1"]["ppa"])
    checked = 0
    for cid, cand in final.items():
        if cand["status"] != "measured" or cand["fitness"] is None:
            continue
        got = compute_fitness(base, PPA.model_validate(cand["ppa"]), "area", "c1")
        recorded = cand["fitness"]
        assert got.score == pytest.approx(recorded["score"], abs=0.01), cid
        for metric in ("area", "timing", "power"):
            assert getattr(got.gains, metric) == pytest.approx(recorded["gains"][metric], abs=0.01), (cid, metric)
            assert getattr(got.weights, metric) == pytest.approx(recorded["weights"][metric], abs=1e-4), (cid, metric)
        checked += 1
    assert checked >= 5


def test_missing_power_renormalizes_the_weights() -> None:
    base, cand = ppa(1000.0, 3.0), ppa(800.0, 3.3)  # area -20%, clock +10% (gain -10)
    fit = compute_fitness(base, cand, "area", "c1")
    assert fit.weights.power == 0.0
    assert fit.weights.area == pytest.approx(0.60 / 0.85, abs=1e-4)
    assert fit.weights.timing == pytest.approx(0.25 / 0.85, abs=1e-4)
    assert fit.gains.power is None
    expected = (0.60 * 20.0 + 0.25 * -10.0) / 0.85
    assert fit.score == pytest.approx(expected, abs=0.01)
    assert fit.score == pytest.approx(web_recompute(base, cand, "area"), abs=0.01)
    # The UI breakdown rows (weight x gain) add up to the score.
    rows = fit.weights.area * fit.gains.area + fit.weights.timing * fit.gains.timing  # type: ignore[operator]
    assert rows == pytest.approx(fit.score, abs=0.02)


@pytest.mark.parametrize("priority", ["area", "speed", "power"])
def test_baseline_against_itself_is_zero(priority: str) -> None:
    p = ppa(1840.0, 3.2)
    fit = compute_fitness(p, p, priority, "c1")  # type: ignore[arg-type]
    assert fit.score == 0.0
    assert fit.gains.area == 0.0


def test_no_measurable_metric_gives_zero_not_an_error() -> None:
    fit = compute_fitness(ppa(None, None), ppa(None, None), "area", "c1")
    assert fit.score == 0.0
    assert (fit.weights.area, fit.weights.timing, fit.weights.power) == (0.0, 0.0, 0.0)


def test_priority_changes_the_ranking() -> None:
    base = ppa(1000.0, 3.0)
    small = candidate("c2", 700.0, 3.6, base=base, priority="area")  # smaller, slower
    fast = candidate("c3", 1100.0, 2.4, base=base, priority="area")  # bigger, faster
    assert pick_winner([small, fast]) == "c2"
    small_s = candidate("c2", 700.0, 3.6, base=base, priority="speed")
    fast_s = candidate("c3", 1100.0, 2.4, base=base, priority="speed")
    assert pick_winner([small_s, fast_s]) == "c3"


def test_elites_exclude_failures_and_rejected_and_ties_prefer_smaller_area() -> None:
    base = ppa(1000.0, 3.0)
    c1 = candidate("c1", 1000.0, 3.0, base=base)
    c2 = candidate("c2", 900.0, 3.0, base=base)
    c3 = candidate("c3", 900.0, 3.0, base=base)
    failed = candidate("c4", 500.0, 3.0, base=base, status="fail")
    rejected = candidate("c5", 400.0, 3.0, base=base, status="rejected")
    pool = [c1, c2, c3, failed, rejected]
    assert [c.id for c in rankable(pool)] == ["c2", "c3", "c1"]  # equal score and area: older first
    assert select_elites(pool, 2) == ["c2", "c3"]
    assert pick_winner(pool) == "c2"
    assert pick_winner(pool, exclude=["c2", "c3"]) == "c1"
    assert pick_winner([failed, rejected]) is None
    assert isinstance(c2.fitness, Fitness)


def test_gains_round_half_up_like_the_web() -> None:
    # (3.2 - 2.7) / 3.2 is exactly 15.625: Python's round() gives 15.62, the web's Math.round gives 15.63.
    fit = compute_fitness(ppa(1000.0, 3.2), ppa(1000.0, 2.7), "area", "c1")
    assert fit.gains.timing == 15.63
