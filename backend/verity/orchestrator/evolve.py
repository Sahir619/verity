"""Evolve rounds, winner selection and the EQY prove stage (CONTRACTS 6.2 steps 10 and 11).

REvolution-lite (backend/ARCHITECTURE.md section 5): round 1 makes 3 children of v1 (SIMPLIFY, OPTIMIZE, EXPLORE),
generated and evaluated in parallel; round 2 (optional) makes 2 children of the current elites. Python decides every
branch. Children never see the golden model, and only candidate events and captions are sent during a round.
"""

import asyncio
import logging

from ..contracts import WinnerOverrideInput, WinnerOverrideSteer
from ..core import captions as cap
from ..core.fitness import result_key
from ..core.llm_types import TriedVersion
from ..core.rtl import Rtl
from ..core.errors import CreditsExhausted
from ..core.types import (
    RunContext,
    BudgetExhausted,
    EquivalenceResult,
    LLMError,
    RoleOutputError,
    RunStopped,
    ToolInfraError,
    not_run_equivalence,
)
from . import build, judge
from .candidates import Cand
from .state import RunState

log = logging.getLogger("verity.orchestrator")


# ======================================================================
# rounds
# ======================================================================
async def evolve_stage(st: RunState) -> None:
    """Round 1, then round 2 when it is worth it. Leaves `stage evolve` active (the winner step closes it)."""
    ctx = st.ctx
    ev = ctx.settings.evolution
    limits = ctx.ledger.limits
    base = st.baseline
    assert base is not None and base.ppa is not None

    if st.budget_blocked():
        await _skip_evolve(st, "budget reached")
        return

    await st.check_credits()
    await st.save_checkpoint("evolve", 1)

    strategies = list(ev.round1_strategies)[: ev.round1_children]
    ran = await _round(st, 1, [(base.id, s) for s in strategies], from_elites=False)
    if not ran:
        await _skip_evolve(st, "no room for more versions")
        return

    improved = judge.improved_on_baseline(st.models(), base.id)
    if not improved:
        await st.em.caption(cap.no_improvement(base.label), cap.CH_ROUND1)

    if limits.max_rounds < 2:
        return
    if ev.round2_requires_improvement and not improved:
        return
    ctx.check_stop()
    if st.budget_blocked():
        return
    if not ctx.ledger.can_count("round") or not ctx.ledger.can_count("candidate"):
        return

    await st.check_credits()
    await st.save_checkpoint("evolve", 2)

    elites = st.current_elites()
    if not elites:
        return
    assignment = []
    for i in range(ev.round2_children):
        parent = st.cands[elites[i % len(elites)]]
        strategy = parent.created_by if parent.created_by != "INIT" else "OPTIMIZE"
        assignment.append((parent.id, strategy))
    await _round(st, 2, assignment, from_elites=True)


async def _skip_evolve(st: RunState, why: str) -> None:
    await st.em.stage("evolve", "skipped", detail=why)


async def _round(st: RunState, round_n: int, assignment: list[tuple[str, str]], *, from_elites: bool) -> bool:
    """Create the children, evaluate them in parallel, pick the elites. False when no child could be created."""
    ctx, em = st.ctx, st.em
    limits = ctx.ledger.limits
    chapter = cap.round_chapter(round_n)

    children: list[Cand] = []
    for parent_id, strategy in assignment:
        if not ctx.ledger.can_count("candidate"):
            break
        children.append(st.new_candidate(round_n=round_n, parent_id=parent_id, created_by=strategy))  # type: ignore[arg-type]
    if not children:
        return False

    ctx.ledger.count("round")
    st.evolve_started = True
    parents = list(dict.fromkeys(c.parent_id for c in children if c.parent_id))
    await em.stage("evolve", "active", detail=f"R{round_n} of {limits.max_rounds}", round_n=round_n)
    await em.round(round_n, parents)
    await em.caption(
        cap.round_started(round_n, len(children), [st.cands[p].label for p in parents], from_elites), chapter
    )

    for child in children:
        child.reason = f"{child.created_by.title()} version of {st.cands[child.parent_id].label}"  # type: ignore[index]
        await em.add_node(child.id, "candidate", "tree", child.label, ref_id=child.id)
        await em.connect(child.parent_id, child.id, "lineage")  # type: ignore[arg-type]
        await em.candidate(child)

    results = await asyncio.gather(*(_child(st, child) for child in children), return_exceptions=True)
    for outcome in results:
        if isinstance(outcome, BaseException):  # _child handles every expected failure; Stop and cancel must surface
            raise outcome

    await _mark_copies(st)
    elite_ids = st.current_elites()
    await em.round(round_n, parents, elite_ids)
    await st.apply_elites(elite_ids)
    if any(c.status == "measured" for c in children):
        await em.caption(cap.elites_kept([st.cands[i].label for i in elite_ids]), chapter)
    return True


async def _mark_copies(st: RunState) -> None:
    """A version whose synthesis gives the same cell count, area and clock period as an earlier measured version
    (v1 included) is a copy of it: no new idea. Run once per round, in version order, so the older one is always the
    original, whatever order the parallel children finished in. The version stays "measured" and keeps its score (so
    the older one wins any tie and round 2 cannot start on a copy alone); only its reason says it is a copy. It is
    never repaired again, and the winner pick never chooses it over the original or proves it again."""
    seen: dict[tuple[int, float, float], Cand] = {}
    for cand in sorted(st.cands.values(), key=lambda c: c.number):
        key = result_key(cand.ppa) if cand.status == "measured" else None
        if key is None:
            continue
        original = seen.setdefault(key, cand)
        if original is not cand and cand.copy_of is None:
            cand.copy_of = original.id
            cand.reason = f"Same result as {original.label}"
            await st.em.candidate(cand)


async def _child(st: RunState, child: Cand) -> None:
    """Generate, verify (with its bounded repair), measure. Every expected failure rejects this child only."""
    ctx, em = st.ctx, st.em
    chapter = cap.round_chapter(child.round)
    base = st.baseline
    assert base is not None and base.ppa is not None
    try:
        parent = st.cands[child.parent_id]  # type: ignore[index]
        assert parent.rtl_obj is not None and parent.ppa is not None
        limit = ctx.ledger.exhausted()
        if limit is not None:
            raise BudgetExhausted(limit)
        ctx.check_stop()
        extra = _explore_idea(st, child, parent)
        with ctx.busy():
            out = await ctx.roles.optimize(
                ctx.llm,
                ctx.pack,
                parent.rtl_obj.text,
                parent.ppa,
                base.ppa,
                ctx.priority,
                child.created_by,
                parent_measure=parent.measure,
                tried=_tried(st, child, parent),
                **extra,
            )
        await st.credits()
        if not await build.evaluate(st, child, out, baseline=False):
            return
        if not await build.measure(st, child, baseline=False):
            return
        await build.publish_reports(st, child)
        assert child.ppa is not None
        await em.caption(cap.child_measured(child.label, base.label, child.ppa, base.ppa), chapter)
    except (asyncio.CancelledError, RunStopped):
        raise
    except BudgetExhausted as exc:
        st.note_budget(exc.limit)
        await _reject(st, child, chapter)
    except (LLMError, RoleOutputError, ToolInfraError) as exc:
        log.warning("run %s: %s rejected: %s", ctx.run_id, child.label, exc)
        await _reject(st, child, chapter)
    except Exception:
        log.exception("run %s: %s failed unexpectedly", ctx.run_id, child.label)
        await _reject(st, child, chapter)


def _tried(st: RunState, child: Cand, parent: Cand) -> list[TriedVersion]:
    """The other finished versions of this run (not v1, not the parent, not this child), oldest first: their strategy,
    their reason and, from the measurements, how they compare with v1, or that they were rejected. Versions still in
    flight are left out, so round 1 (all children run at once) has nothing to list; round 2 sees all of round 1."""
    out: list[TriedVersion] = []
    for other in sorted(st.cands.values(), key=lambda c: c.number):
        if other.parent_id is None or other.id in (child.id, parent.id):
            continue
        if other.status == "measured" and other.ppa is not None:
            out.append(TriedVersion(other.label, other.created_by, other.reason, other.ppa))
        elif other.status == "rejected":
            out.append(TriedVersion(other.label, other.created_by, other.reason, None))
    return out


def _explore_idea(st: RunState, child: Cand, parent: Cand) -> dict[str, str]:
    """EXPLORE children get one technique from the research that the parent does not use (our own text from the
    fixed list), instead of the generic EXPLORE idea. Recorded in ctx.techniques for the run outcome."""
    ctx = st.ctx
    if child.created_by != "EXPLORE" or ctx.references is None:
        return {}
    from ..research.techniques import mentioned_in

    used = {t for cid, t in ctx.techniques.items() if cid == parent.id}
    if parent.parent_id is None and st.plan is not None:  # v1 follows the plan: read its approach from the plan text
        plan_text = " ".join([st.plan.summary, *(f"{b.name} {b.reason}" for b in st.plan.blocks)])
        used |= mentioned_in(ctx.pack.id, plan_text)
    used |= {t for cid, t in ctx.techniques.items() if st.cands.get(cid) and st.cands[cid].round == child.round}
    tech = ctx.references.explore_idea(used)
    if tech is None:
        return {}
    ctx.techniques[child.id] = tech.id
    return {"idea": f"{tech.label}: {tech.idea}."}


async def _reject(st: RunState, child: Cand, chapter: str) -> None:
    if child.status in ("rejected", "measured"):
        return
    child.reject()
    await st.em.candidate(child)
    await st.em.caption(cap.child_rejected(child.label), chapter)


# ======================================================================
# winner and prove
# ======================================================================
async def choose_and_prove(st: RunState, *, prove: bool) -> str:
    """AI pick by fitness, then EQY on it (full mode). Returns the final winner id."""
    ctx, em = st.ctx, st.em
    base = st.baseline
    assert base is not None

    pick = judge.winner(st.models()) or base.id
    await em.winner(pick, "ai", pick)
    ctx.ai_winner_id = pick
    if st.evolve_started:
        await em.stage("evolve", "done")
    if pick == base.id:
        await em.caption(cap.baseline_wins(base.label))
    else:
        await em.caption(cap.winner(st.cands[pick].label))

    if not prove:
        return pick  # fast mode: the caller already marked evolve and prove as skipped
    return await _prove(st, pick)


async def run_eqy(ctx: RunContext, gold: Rtl, gate: Rtl) -> EquivalenceResult:
    """EQY gold vs gate. An infrastructure error becomes a status "error" result (maps to inconclusive)."""
    try:
        with ctx.busy():
            return await ctx.tools.check_equivalence(gold, gate, ctx.pack)
    except ToolInfraError as exc:
        log.warning("run %s: EQY infrastructure error: %s", ctx.run_id, exc)
        return EquivalenceResult(status="error", message=str(exc))


async def _run_eqy(st: RunState, gold: Cand, gate: Cand) -> EquivalenceResult:
    assert gold.rtl_obj is not None and gate.rtl_obj is not None
    return await run_eqy(st.ctx, gold.rtl_obj, gate.rtl_obj)


async def _prove(st: RunState, winner_id: str) -> str:
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    base = st.baseline
    assert base is not None

    if winner_id == base.id:
        await em.stage("prove", "skipped", detail="v1 is the winner")
        return winner_id

    winner = st.cands[winner_id]
    if not pack.eqy.enabled:
        winner.eqy = not_run_equivalence(base.id, "Equivalence checking is turned off for this task.")
        await em.candidate(winner)
        await em.stage("prove", "skipped", detail="equivalence checking is off for this task")
        return winner_id
    if ctx.ledger.exhausted() == "max_wall_s":
        st.note_budget("max_wall_s")
        winner.eqy = not_run_equivalence(base.id, "Not run: the time limit was reached.")
        await em.candidate(winner)
        await em.stage("prove", "skipped", detail="time limit reached")
        return winner_id

    await em.stage("prove", "active")
    max_attempts = ctx.settings.evolution.max_prove_attempts
    excluded: list[str] = []
    attempts = 0
    current = winner_id

    while True:
        ctx.check_stop()
        attempts += 1
        win = st.cands[current]
        await em.caption(cap.proving(win.label, base.label), cap.CH_PROVE)
        result = await _run_eqy(st, base, win)
        eq = result.to_equivalence(base.id)
        win.eqy = eq
        win.eqy_timed_out = result.status == "timeout"
        await em.candidate(win)
        report = result.log or result.message or eq.status
        await em.artifact(f"reports/eqy_{win.label}_vs_v1.log", report)
        await st.save("final/eqy.log", report)

        if eq.status == "proven":
            await em.connect(win.id, base.id, "proof", "Proven equal (EQY)")
            await em.caption(cap.proven(base.label), cap.CH_PROVE)
            break
        if eq.status != "not_equivalent":  # inconclusive (timeout and error land here): the winner stands, unproven
            await em.caption(cap.not_proven(), cap.CH_PROVE)
            break

        # not_equivalent: demote, pick the next best, prove again (bounded), then fall back to v1.
        win.reject()
        await em.candidate(win)
        excluded.append(win.id)
        await st.refresh_elites()
        nxt = judge.winner(st.models(), excluded) or base.id
        if attempts >= max_attempts:
            nxt = base.id
        await em.caption(cap.not_equivalent(win.label, st.cands[nxt].label), cap.CH_PROVE)
        await em.winner(nxt, "ai", nxt)
        ctx.ai_winner_id = nxt
        current = nxt
        if nxt == base.id:
            break

    await em.stage("prove", "done")
    return current


# ======================================================================
# human override (steer after the AI pick)
# ======================================================================
async def apply_winner_override(st: RunState, current: str) -> str:
    """Honor a winner_override steer that arrived after the AI pick. The last valid one wins."""
    ctx, em = st.ctx, st.em
    while not ctx.steer.empty():
        request = ctx.steer.get_nowait()
        if not isinstance(request, WinnerOverrideSteer):
            continue
        cand = st.cands.get(request.candidate_id)
        if cand is None or cand.status != "measured" or cand.id == current:
            continue
        await em.human(WinnerOverrideInput(t_ms=ctx.now_ms(), candidate_id=cand.id))
        await em.winner(cand.id, "user", ctx.ai_winner_id or current)
        current = cand.id
    return current
