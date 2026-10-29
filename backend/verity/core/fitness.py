"""Fitness, elites and winner (frontend ARCHITECTURE 8.3). Pure code; the Judge. No LLM output is ever used here.

    gain_m  = 100 * (v1_m - c_m) / v1_m        m in {area, clock period, power}; lower is better for all
    fitness = w_area * gain_area + w_timing * gain_timing + w_power * gain_power
    missing metric (null for v1 or c) -> drop it, renormalize the remaining weights to sum 1
    FAIL or rejected -> fitness null, never ranked
    elites: best 2 by fitness among measured versions so far (v1 counts, score 0)
    winner: highest fitness among versions no worse than v1 on the run's goal metric (area for "area", clock period
            for "speed", power for "power", zero tolerance: equal is allowed, any increase is not); tie -> smaller area,
            then the older version. v1 is always eligible. A missing goal metric (v1 or the version) skips the check.
            The goal check is for the final winner only; elites keep the plain ranking.

Recorded `Fitness.weights` are the EFFECTIVE (renormalized) weights, with 0 for a dropped metric, so the web
breakdown rows (weight x gain) sum to the score. Gains are rounded to 2 decimals, weights to 4, and the score
(computed from unrounded values) to 2. The web dev check recomputes from PPA within 0.01.
"""

import math
from collections.abc import Sequence

from ..contracts import PPA, Candidate, Fitness, FitnessGains, FitnessWeights, Priority

BASE_WEIGHTS: dict[Priority, dict[str, float]] = {
    "area": {"area": 0.60, "timing": 0.25, "power": 0.15},
    "speed": {"area": 0.25, "timing": 0.60, "power": 0.15},
    "power": {"area": 0.25, "timing": 0.15, "power": 0.60},
}


def _round(x: float, digits: int) -> float:
    """Round half up like the web's Math.round (Python's round() is half-even: 15.625 gives 15.62, the web 15.63)."""
    scale = 10**digits
    return math.floor(x * scale + 0.5) / scale


def _gain(baseline: float | None, value: float | None) -> float | None:
    if baseline is None or value is None or baseline <= 0:
        return None
    return 100.0 * (baseline - value) / baseline


def compute_fitness(baseline: PPA, candidate: PPA, priority: Priority, baseline_id: str) -> Fitness:
    w = BASE_WEIGHTS[priority]
    gains = {
        "area": _gain(baseline.area_um2, candidate.area_um2),
        "timing": _gain(baseline.clock_period_ns, candidate.clock_period_ns),
        "power": _gain(baseline.power_mw, candidate.power_mw),
    }
    present = {m: w[m] for m, g in gains.items() if g is not None}
    total = sum(present.values())
    effective = {m: (present[m] / total if m in present and total > 0 else 0.0) for m in ("area", "timing", "power")}
    score = sum(effective[m] * g for m, g in gains.items() if g is not None) if total > 0 else 0.0
    return Fitness(
        score=_round(score, 2) + 0.0,  # + 0.0 turns -0.0 into 0.0
        priority=priority,
        weights=FitnessWeights(**{m: _round(v, 4) for m, v in effective.items()}),
        gains=FitnessGains(**{m: (_round(g, 2) + 0.0 if g is not None else None) for m, g in gains.items()}),
        baseline_id=baseline_id,
    )


def _candidate_number(c: Candidate) -> int:
    digits = "".join(ch for ch in c.id if ch.isdigit())
    return int(digits) if digits else 0


def _rank_key(c: Candidate) -> tuple[float, float, int]:
    assert c.fitness is not None
    area = c.ppa.area_um2 if c.ppa and c.ppa.area_um2 is not None else float("inf")
    return (-c.fitness.score, area, _candidate_number(c))


def rankable(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Measured, not rejected, with a fitness. Ordered best first (score desc, then smaller area, then older)."""
    pool = [c for c in candidates if c.status == "measured" and c.fitness is not None]
    return sorted(pool, key=_rank_key)


def select_elites(candidates: Sequence[Candidate], k: int = 2) -> list[str]:
    return [c.id for c in rankable(candidates)[:k]]


GOAL_METRIC: dict[Priority, str] = {"area": "area_um2", "speed": "clock_period_ns", "power": "power_mw"}


def goal_value(ppa: PPA | None, priority: Priority) -> float | None:
    return getattr(ppa, GOAL_METRIC[priority], None) if ppa is not None else None


def worse_on_goal(candidate: Candidate, baseline: Candidate | None) -> bool:
    """True when the version is worse than v1 on the run's goal metric (lower is better for all three). Never true for
    v1 itself, or when either value is missing (then today's plain ranking applies)."""
    if baseline is None or candidate.id == baseline.id or candidate.fitness is None:
        return False
    priority = candidate.fitness.priority
    value, base_value = goal_value(candidate.ppa, priority), goal_value(baseline.ppa, priority)
    return value is not None and base_value is not None and value > base_value


def result_key(ppa: PPA | None) -> tuple[int, float, float] | None:
    """Cell count, area and clock period: two versions with the same key synthesized to the same result (a copy).
    None when any of the three is missing, so an incomplete measurement is never called a copy."""
    if ppa is None or ppa.cell_count is None or ppa.area_um2 is None or ppa.clock_period_ns is None:
        return None
    return (ppa.cell_count, ppa.area_um2, ppa.clock_period_ns)


def pick_winner(candidates: Sequence[Candidate], exclude: Sequence[str] = ()) -> str | None:
    """AI pick. `exclude` holds ids demoted by EQY not_equivalent. A version worse than v1 on the goal is never picked,
    and neither is a copy (same cells, area and clock) of a demoted version: it would only be proven unequal again."""
    by_id = {c.id: c for c in candidates}
    demoted = {key for i in exclude if i in by_id and (key := result_key(by_id[i].ppa)) is not None}
    for c in rankable(candidates):
        if c.id in exclude:
            continue
        assert c.fitness is not None
        base = by_id.get(c.fitness.baseline_id)
        if base is not None and c.id != base.id and (result_key(c.ppa) in demoted or worse_on_goal(c, base)):
            continue
        return c.id
    return None
