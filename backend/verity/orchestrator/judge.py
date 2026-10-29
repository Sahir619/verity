"""The Judge (ARCHITECTURE 3): fitness, elites and the winner, as pure code. No LLM output is ever used here.

The formula is frontend ARCHITECTURE 8.3 and is implemented once, in `core/fitness.py` (CONTRACTS section 7: do not
reimplement). It was compared line by line with `frontend/src/lib/run/fitness.ts` `recomputeFitness`:
  - weights per priority {area 0.60/0.25/0.15, speed 0.25/0.60/0.15, power 0.25/0.15/0.60}: identical,
  - gain = 100 * (v1 - c) / v1 per metric, lower is better: identical,
  - a metric that is null for v1 or for the candidate is dropped and the rest renormalized: identical
    (power is null for older records or when the power estimate fails, then the rest are renormalized to sum 1),
  - a total weight of 0 gives score 0: identical.
The extra part in core/fitness.py is the recorded `weights` field, which holds the EFFECTIVE (renormalized) weights so
the web breakdown rows add up to the score.
"""

from collections.abc import Sequence

from ..contracts import PPA, Candidate, Fitness, Priority
from ..core.fitness import compute_fitness, pick_winner, rankable, select_elites


def baseline_fitness(ppa: PPA, priority: Priority, baseline_id: str) -> Fitness:
    """v1 against itself: score 0 by definition."""
    return compute_fitness(ppa, ppa, priority, baseline_id)


def candidate_fitness(baseline: PPA, ppa: PPA, priority: Priority, baseline_id: str) -> Fitness:
    return compute_fitness(baseline, ppa, priority, baseline_id)


def elites(candidates: Sequence[Candidate], k: int) -> list[str]:
    """Best k by fitness among measured versions (v1 counts, at 0). Best first."""
    return select_elites(candidates, k)


def winner(candidates: Sequence[Candidate], exclude: Sequence[str] = ()) -> str | None:
    """Highest fitness; tie goes to the smaller area, then the older candidate."""
    return pick_winner(candidates, exclude)


def improved_on_baseline(candidates: Sequence[Candidate], baseline_id: str) -> bool:
    """True when at least one measured, non-baseline version scores above v1's 0."""
    return any(c.id != baseline_id and c.fitness is not None and c.fitness.score > 0 for c in rankable(candidates))
