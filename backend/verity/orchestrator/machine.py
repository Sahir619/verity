"""The orchestrator state machine (backend ARCHITECTURE section 5, CONTRACTS section 6).

    spec -> plan (+ gate, feasibility) -> lock -> rtl -> lint/sim/fix -> synth(v1)
         -> evolve round 1 (-> round 2) -> winner -> prove (EQY) -> human reference -> done -> sign-off

The sign-off (signoff.py) starts after `run done` and the Done caption; its artifacts follow them.

Contract with the run manager (core/context.py):
- emits `run running` first and exactly one terminal event: done | error | cap_reached,
- never emits `stopped` and never swallows asyncio.CancelledError or RunStopped,
- sets ctx.outcome before the terminal event,
- the model is never asked what to do next: every branch below is an `if` on tool output, counters or budgets.
"""

import asyncio
import json
import logging

from ..contracts import to_wire
from ..core import captions as cap
from ..core.errors import CreditsExhausted
from ..research.outcomes import record_run_outcome
from ..core.types import (
    BudgetExhausted,
    LLMError,
    PackIntegrityError,
    RoleOutputError,
    RunContext,
    RunOutcome,
    RunPatch,
    RunStopped,
    ToolInfraError,
)
from . import build, evolve, human_ref, judge, signoff
from .candidates import FIRST_VERSION_REASON
from .events import Emitter
from .plan_stage import StageFailed, plan_stage, spec_stage
from .state import BASELINE_ID, RunState

log = logging.getLogger("verity.orchestrator")


async def run_orchestrator(ctx: RunContext, *, resume: bool = False) -> None:
    st = RunState(ctx=ctx, em=Emitter(ctx))
    try:
        record = await ctx.store.get_run(ctx.run_id)
        st.image_digest = record.tool_image_digest if record else ""
    except Exception:
        log.exception("run %s: could not read the run record", ctx.run_id)

    try:
        await _run(st, resume=resume)
    except (asyncio.CancelledError, RunStopped):
        raise
    except CreditsExhausted:
        log.info("run %s: credits exhausted mid-run, saving checkpoint", ctx.run_id)
        await _checkpoint_stopped(st, "credits_exhausted", "Run paused at checkpoint: run credits exhausted. Refill credits to continue.")
    except StageFailed as exc:
        await _end_error(st, exc.reason, announced=exc.announced)
    except BudgetExhausted as exc:
        await _end_budget(st, exc.limit)
    except PackIntegrityError:
        log.exception("run %s: task pack integrity failure", ctx.run_id)
        await _end_error(st, "the task pack failed its integrity check")
    except LLMError as exc:
        log.warning("run %s: model call failed: %s", ctx.run_id, exc)
        if exc.retryable and st.evolve_started:
            log.info("run %s: network problem mid-run, saving checkpoint", ctx.run_id)
            await _checkpoint_stopped(st, "network_error", "Run paused at checkpoint: a network problem interrupted a model call. Resume to continue.")
        else:
            plain = cap.model_failed(str(exc), exc.status, exc.retryable)
            await _end_error(st, plain or "the model call failed: " + cap.clean(str(exc), 160))
    except RoleOutputError as exc:
        log.warning("run %s: unusable model answer: %s", ctx.run_id, exc)
        await _end_error(st, "the model's answer could not be used: " + cap.clean(str(exc), 160))
    except ToolInfraError as exc:
        log.warning("run %s: sandbox failure: %s | %s", ctx.run_id, exc, exc.stderr_tail)
        if st.evolve_started:
            log.info("run %s: sandbox problem mid-run, saving checkpoint", ctx.run_id)
            await _checkpoint_stopped(st, "sandbox_error", "Run paused at checkpoint: the test machine had a problem. Resume to continue.")
        else:
            await _end_error(st, cap.sandbox_failed(f"{exc} {exc.stderr_tail}"))


async def _checkpoint_stopped(st: RunState, reason: str, message: str) -> None:
    """Save a checkpoint and end the run as `stopped` with `reason`, so POST /runs/{id}/resume can continue it
    from here instead of the run being lost (credits exhausted, a network problem, or a sandbox problem)."""
    ctx = st.ctx
    await st.save_checkpoint("evolve")
    ctx.outcome = _outcome(st, "stopped", winner=None, reason=reason)  # the run manager ends a run without one as an error
    await st.em.caption(message)
    await st.em.run("stopped", message=message)
    await ctx.store.update_run(ctx.run_id, RunPatch(status="stopped", error_reason=reason))


async def _run(st: RunState, *, resume: bool = False) -> None:
    ctx, em = st.ctx, st.em

    if resume:
        raw_chk = await ctx.store.read_run_file(ctx.run_id, "checkpoint.json")
        if raw_chk:
            chk_data = json.loads(raw_chk.decode("utf-8"))
            await st.load_checkpoint_data(chk_data)
            for cand in st.cands.values():
                await em.candidate(cand)
            await em.caption("Resumed run from checkpoint.")
            if st.plan:
                await em.stage("plan", "done")
            await em.stage("lock", "done")
            await em.stage("rtl", "done")  # what _baseline_stage marks when v1 is built

    if not resume:
        await spec_stage(st)
        await plan_stage(st)
        await _lock_stage(st)
        await _baseline_stage(st)

    await st.check_credits()

    if ctx.mode == "fast":
        await em.stage("evolve", "skipped")
        await em.stage("prove", "skipped")
        winner = await evolve.choose_and_prove(st, prove=False)
    else:
        await evolve.evolve_stage(st)
        winner = await evolve.choose_and_prove(st, prove=True)

    winner = await evolve.apply_winner_override(st, winner)
    await _close(st, winner)


async def _close(st: RunState, winner: str) -> None:
    """The human reference proof, then the result (`run done` and the Done caption), then the sign-off. The sign-off's
    sandbox calls start only once the result is out, so they never compete with the human reference proof that gates
    `run done`; its artifacts and caption follow the result (CONTRACTS 6.3)."""
    human_eqy = await human_ref.measure_human_ref(st, winner)
    await _finish(st, winner)
    pending = signoff.start(st, winner)
    try:
        await signoff.finish(st, pending, human_eqy)
    finally:
        await signoff.cancel(pending)


# ======================================================================
# lock
# ======================================================================
async def _lock_stage(st: RunState) -> None:
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    ctx.check_stop()
    await em.stage("lock", "active")
    await em.add_node("golden", "golden", "plan", "Golden model", ref_id="golden")
    await em.update_status("golden", "pending")
    try:
        await asyncio.to_thread(pack.reverify)
    except PackIntegrityError:
        log.exception("run %s: task pack %s failed re-verification", ctx.run_id, pack.id)
        await em.stage("lock", "failed", detail="integrity check failed")
        raise StageFailed("the task pack failed its integrity check", announced=False) from None
    await em.artifact("tests/golden_model.py", pack.golden_source())
    await em.artifact("tests/test_dut.py", pack.testbench_source())
    await em.update_status("golden", "locked")
    await em.stage("lock", "done")
    await em.caption(cap.locked(pack.test_count), cap.CH_LOCK)


# ======================================================================
# baseline (v1): rtl, verify with bounded repair, synth
# ======================================================================
async def _baseline_stage(st: RunState) -> None:
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    assert st.plan is not None
    ctx.check_stop()

    await em.stage("rtl", "active")
    c1 = st.new_candidate(round_n=0, parent_id=None, created_by="INIT")
    assert c1.id == BASELINE_ID
    c1.reason = FIRST_VERSION_REASON
    await em.add_node(c1.id, "candidate", "tree", c1.label, ref_id=c1.id)
    await em.connect("golden", c1.id, "checks", "checks every version")
    await em.candidate(c1)
    await em.caption(cap.writing_v1(), cap.CH_BUILD)

    if ctx.settings.baseline.samples > 1:
        out = await build.sample_baseline(st, c1)  # R4: k parallel first attempts, best one continues
    else:
        with ctx.busy():
            out = await ctx.roles.generate_rtl(ctx.llm, pack, st.plan, ctx.priority, **build.prior_kwargs(ctx))
        await st.credits()
    await em.stage("rtl", "done")

    if not await build.evaluate(st, c1, out, baseline=True):
        if st.budget_limit is not None:
            raise BudgetExhausted(st.budget_limit)
        raise StageFailed("no passing baseline", announced=True)

    if not await build.measure(st, c1, baseline=True):
        raise StageFailed("the first passing version did not synthesize", announced=True)

    for block in st.plan.blocks:
        await em.update_status(block.id, "built")
    assert c1.ppa is not None
    await em.caption(cap.baseline_measured(c1.label, c1.ppa), cap.CH_BUILD)
    await build.publish_reports(st, c1)


# ======================================================================
# endings
# ======================================================================
def _counts(st: RunState) -> tuple[int, int, int, int]:
    versions = len(st.cands)
    fixes = sum(c.fix_count for c in st.cands.values())
    rejected = sum(1 for c in st.cands.values() if c.status == "rejected")
    return versions, fixes, rejected, st.em.human_inputs


def _outcome(st: RunState, status, *, winner: str | None, reason: str | None) -> RunOutcome:
    versions, fixes, rejected, humans = _counts(st)
    return RunOutcome(
        status=status,
        winner_id=winner,
        baseline_id=BASELINE_ID if BASELINE_ID in st.cands else None,
        error_reason=reason,
        versions=versions,
        fixes=fixes,
        rejected=rejected,
        human_inputs=humans,
    )


async def _publish_summary(st: RunState, status: str, winner: str | None) -> None:
    ctx = st.ctx
    payload = {
        "runId": ctx.run_id,
        "presetId": ctx.pack.id,
        "priority": ctx.priority,
        "mode": ctx.mode,
        "packSha256": ctx.pack.sha256,
        "outcome": status,
        "budgetLimit": st.budget_limit,
        "winnerId": winner,
        "baselineId": BASELINE_ID if BASELINE_ID in st.cands else None,
        "spend": ctx.ledger.snapshot().model_dump(mode="json"),
        "humanInputs": st.em.human_inputs,
        "versions": [
            {
                "id": c.id,
                "label": c.label,
                "round": c.round,
                "parentId": c.parent_id,
                "createdBy": c.created_by,
                "status": c.status,
                "fixCount": c.fix_count,
                "isElite": c.is_elite,
                "ppa": to_wire(c.ppa) if c.ppa else None,
                "fitness": to_wire(c.fitness) if c.fitness else None,
                "eqy": c.eqy.status if c.eqy else None,
            }
            for c in st.cands.values()
        ],
    }
    calls = getattr(ctx.llm, "calls", None)
    if calls:  # R3: finish reason and reasoning tokens of every model call
        await st.save("final/llm-calls.json", json.dumps(calls, indent=2), "application/json")
    body = json.dumps(payload, indent=2)
    await st.em.artifact("reports/summary.json", body)
    await st.save("final/summary.json", body, "application/json")
    if winner is not None:
        win = st.cands[winner]
        if win.rtl_obj is not None:
            await st.save("final/winner.v", win.rtl_obj.text, "text/x-verilog")
    # One run_outcome row (plus strategy rows) for later runs to read; the run manager upserts it again at finalize.
    await record_run_outcome(ctx.store, ctx, status=status, winner_id=winner)


async def _finish(st: RunState, winner: str) -> None:
    """Normal ending: done, or cap_reached when a budget limit cut the run short (CONTRACTS 6.2 step 12)."""
    ctx, em = st.ctx, st.em
    st.final_winner_id = winner
    limit = st.budget_limit
    status = "cap_reached" if limit else "done"
    reason = f"budget limit reached: {limit}" if limit else None
    ctx.outcome = _outcome(st, status, winner=winner, reason=reason)

    await em.credits()
    await _publish_summary(st, status, winner)
    versions, fixes, rejected, humans = _counts(st)
    if limit:
        await em.caption(cap.budget_reached(limit))
        await em.run("cap_reached", message=reason)
    else:
        await em.run("done")
    await em.caption(cap.done(versions, fixes, rejected, humans), cap.CH_DONE)


async def _end_budget(st: RunState, limit: str) -> None:
    """A role call was refused before any winner was chosen. Finish with whatever is measured."""
    st.note_budget(limit)
    winner = judge.winner(st.models())
    if winner is not None:
        await _close(st, winner)
        return
    em = st.em
    reason = f"budget limit reached: {limit}"
    st.ctx.outcome = _outcome(st, "cap_reached", winner=None, reason=reason)
    await em.credits()
    await _publish_summary(st, "cap_reached", None)
    await em.caption(cap.budget_reached(limit))
    await em.run("cap_reached", message=reason)


async def _end_error(st: RunState, reason: str, *, announced: bool = False) -> None:
    em = st.em
    message = cap.clean(reason, 300)
    st.ctx.outcome = _outcome(st, "error", winner=None, reason=message)
    if not announced:
        await em.caption(cap.failed(message))
    await em.credits()
    await _publish_summary(st, "error", None)
    await em.run("error", message=message)
