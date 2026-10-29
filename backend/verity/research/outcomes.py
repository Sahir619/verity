"""Run outcomes (plan claim 2): one run_outcome row and one strategy_outcome row per version tried, written from
both endings of a run (orchestrator _publish_summary and RunManager._finalize) with an idempotent upsert.

Every number is code-measured and stored as a ratio against the run's own v1 (lower is better), so later runs only
compare like with like: lookups also require the same task pack hash and tool image digest.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from ..core.protocols import Store
from ..core.records import CandidateRow, RunOutcomeRow, StrategyOutcomeRow

log = logging.getLogger("verity.research")

_IMPROVED_BELOW = 0.999  # a ratio under this beat v1 on the priority metric


def _rel(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return round(value / base, 4)


def _metric(row: CandidateRow, priority: str) -> float | None:
    if priority == "speed":
        return row.clock_period_ns
    if priority == "power":
        return row.power_mw if row.power_mw is not None else row.area_um2
    return row.area_um2


def _result(row: CandidateRow, v1: CandidateRow | None, priority: str) -> str:
    if row.status == "rejected":
        return "rejected"
    if row.eqy == "not_equivalent":
        return "not_equivalent"
    if row.status != "measured" or v1 is None:
        return "unfinished"
    rel = _rel(_metric(row, priority), _metric(v1, priority))
    return "improved" if rel is not None and rel < _IMPROVED_BELOW else "not_better"


async def record_run_outcome(store: Store, ctx: Any, *, status: str, winner_id: str | None) -> None:
    """Upsert this run's outcome rows. `ctx` is the RunContext. Never raises: a failed write must not end a run."""
    try:
        run = await store.get_run(ctx.run_id)
        if run is None or not getattr(ctx, "outcome_key", ""):
            return
        cands = await store.candidates_for_run(ctx.run_id)
        by_id = {c.id: c for c in cands}
        v1 = next((c for c in cands if c.is_baseline), by_id.get("c1"))
        techniques: dict[str, str] = dict(getattr(ctx, "techniques", {}) or {})
        priority = str(ctx.priority)
        rows = [
            StrategyOutcomeRow(
                run_id=ctx.run_id,
                candidate_id=c.id,
                label=c.label,
                key=ctx.outcome_key,
                pack_sha256=run.task_pack_sha256,
                tool_image_digest=run.tool_image_digest,
                strategy=c.created_by,
                technique=techniques.get(c.id),
                result=_result(c, v1, priority),  # type: ignore[arg-type]
                area_rel=_rel(c.area_um2, v1.area_um2 if v1 else None),
                clock_rel=_rel(c.clock_period_ns, v1.clock_period_ns if v1 else None),
                power_rel=_rel(c.power_mw, v1.power_mw if v1 else None),
            )
            for c in cands
            if not c.is_baseline and c.created_by != "INIT"
        ]
        winner = by_id.get(winner_id) if winner_id else None
        snap = ctx.ledger.snapshot()
        outcome = RunOutcomeRow(
            run_id=ctx.run_id,
            research_id=run.research_id,
            family=run.preset_id,
            params=dict(getattr(ctx, "params_full", {}) or {}),
            key=ctx.outcome_key,
            priority=priority,
            mode=str(ctx.mode),
            status=status,
            partial=status != "done",
            pack_sha256=run.task_pack_sha256,
            tool_image_digest=run.tool_image_digest,
            models=dict(run.models),
            v1_area_um2=v1.area_um2 if v1 else None,
            v1_clock_ns=v1.clock_period_ns if v1 else None,
            v1_power_mw=v1.power_mw if v1 else None,
            winner_id=winner.id if winner else None,
            winner_label=winner.label if winner else None,
            winner_strategy=winner.created_by if winner else None,
            winner_technique=techniques.get(winner.id) if winner else None,
            winner_area_rel=_rel(winner.area_um2, v1.area_um2) if winner and v1 else None,
            winner_clock_rel=_rel(winner.clock_period_ns, v1.clock_period_ns) if winner and v1 else None,
            winner_power_rel=_rel(winner.power_mw, v1.power_mw) if winner and v1 else None,
            winner_eqy=winner.eqy if winner else None,
            versions=len(cands),
            fixes=sum(c.fix_count for c in cands),
            rejected=sum(1 for c in cands if c.status == "rejected"),
            usd=round(snap.usd, 6),
            llm_calls=snap.llm_calls,
            wall_s=round(ctx.elapsed_s(), 1),
            created_at=run.created_at if run.created_at else datetime.now(UTC),
        )
        await store.put_run_outcome(outcome)
        await store.put_strategy_outcomes(ctx.run_id, rows)
    except Exception:
        log.exception("run %s: could not write the run outcome", getattr(ctx, "run_id", "?"))
