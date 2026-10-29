"""Sign-off of the winner (CONTRACTS 6.3, artifact signoff/<candidateId>.json), on every run, fast and full.

It starts once the result is out (after `run done` and the Done caption, so its sandbox calls never compete with the
human reference proof that gates `run done`), and the orchestrator waits for it after that, so it never delays the result. Every part is a sandbox call, never a model call:

  tests     the locked tests on the extra seeds ([signoff] extra_seeds) in one call, plus lint warnings by tag
  strength  planted bugs (fixed rules, core/mutants.py), all simulated in one call; up to max_survivor_proofs bugs
            that pass the tests are checked with the reset-aware EQY against the winner: proven equal is harmless,
            proven different is a miss, anything else is undecided
  waves     the winner's first test case (waves/<candidateId>.json)

eqyVsV1 comes from the prove stage and eqyVsHuman from the human reference proof. Stop cancels it. The deadline is the
smaller of [signoff] max_s and the watchdog's wall-clock cutoff: whatever has not finished by then is reported as not
run, and the artifact is still sent.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from ..contracts import Equivalence, to_json
from ..contracts.signoff import EqyVsHuman, EqyVsV1, SeedTally, Signoff, SignoffLint, SignoffStrength, SignoffTests
from ..core import captions as cap
from ..core.errors import RunStopped
from ..core.mutants import Mutant, plant
from ..core.results import EquivalenceResult, SeedsResult, WaveTrace
from . import evolve, waves
from .candidates import Cand
from .state import RunState

log = logging.getLogger("verity.orchestrator")

SAFETY_MARGIN_S = 15  # finish before the watchdog's hard cutoff
_WARNING_TAG = re.compile(r"^%Warning-([A-Za-z0-9_]+)", re.MULTILINE)


@dataclass(slots=True)
class _Parts:
    seeds: SeedsResult | None = None
    strength: SignoffStrength | None = None
    mutant_rows: list[dict] = field(default_factory=list)
    wave: WaveTrace | None = None


@dataclass(slots=True)
class Pending:
    cand: Cand
    parts: _Parts
    task: "asyncio.Task[None] | None"
    started: float
    skipped: str = ""  # why nothing ran (a plain sentence), or ""


# ======================================================================
# start, wait, cancel
# ======================================================================
def start(st: RunState, winner_id: str) -> Pending | None:
    """Start the sign-off of the final winner in the background. None when it is turned off or there is no winner."""
    ctx = st.ctx
    cand = st.cands.get(winner_id)
    if not ctx.settings.signoff.enabled or cand is None or cand.rtl_obj is None:
        return None
    pending = Pending(cand=cand, parts=_Parts(), task=None, started=time.monotonic())
    if ctx.ledger.exhausted() == "max_wall_s" or _time_left(st, pending) <= 0:
        pending.skipped = "Not run: the time limit was reached."
        return pending
    pending.task = asyncio.ensure_future(_work(st, cand, pending.parts))
    return pending


async def cancel(pending: Pending | None) -> None:
    """Stop the background work (Stop, an error, or a run that ended another way). Safe to call twice."""
    if pending is None or pending.task is None or pending.task.done():
        return
    pending.task.cancel()
    await asyncio.gather(pending.task, return_exceptions=True)


async def finish(st: RunState, pending: Pending | None, human_eqy: Equivalence | None) -> None:
    """Wait (up to the deadline), then emit waves/<id>.json, signoff/<id>.json and the sign-off caption.

    Called after `run done`. Never raises except for Stop and cancel: the run's result is already out."""
    if pending is None:
        return
    ctx = st.ctx
    timed_out = False
    if pending.task is not None:
        done, _ = await asyncio.wait({pending.task}, timeout=max(0.0, _time_left(st, pending)))
        if not done:
            timed_out = True
            await cancel(pending)
        elif not pending.task.cancelled() and pending.task.exception() is not None:
            exc = pending.task.exception()
            if isinstance(exc, RunStopped):
                raise exc
            log.error("run %s: sign-off failed", ctx.run_id, exc_info=exc)
    try:
        report = _report(st, pending, human_eqy, timed_out=timed_out)
        if pending.parts.wave is not None:
            await waves.publish(st, pending.cand, pending.parts.wave, file_name="signoff.waves.json")
        body = to_json(report)
        await st.em.artifact(f"signoff/{pending.cand.id}.json", body)
        await st.save("final/signoff.json", body, "application/json")
        if pending.parts.mutant_rows:
            await st.save_json("final/signoff-mutants.json", pending.parts.mutant_rows)
        await st.em.caption(_caption(st, pending.cand, report), cap.CH_DONE)
    except (asyncio.CancelledError, RunStopped):
        raise
    except Exception:
        log.exception("run %s: could not publish the sign-off", ctx.run_id)


def _time_left(st: RunState, pending: Pending) -> float:
    ctx = st.ctx
    settings = ctx.settings
    by_config = settings.signoff.max_s - (time.monotonic() - pending.started)
    hard_cutoff = ctx.ledger.limits.max_wall_s + settings.timeouts.eqy_s + settings.timeouts.sandbox_overhead_s
    by_wall = hard_cutoff - ctx.elapsed_s() - SAFETY_MARGIN_S
    return min(by_config, by_wall)


# ======================================================================
# the work (background task)
# ======================================================================
async def _work(st: RunState, cand: Cand, parts: _Parts) -> None:
    await asyncio.gather(_seeds(st, cand, parts), _strength(st, cand, parts), _wave(st, cand, parts))


async def _seeds(st: RunState, cand: Cand, parts: _Parts) -> None:
    ctx, pack = st.ctx, st.ctx.pack
    assert cand.rtl_obj is not None
    seeds = [s for s in ctx.settings.signoff.extra_seeds if s != pack.sim.seed]
    if not seeds:
        return
    try:
        with ctx.busy():
            parts.seeds = await ctx.tools.verify_seeds(cand.rtl_obj, pack, seeds)
    except (asyncio.CancelledError, RunStopped):
        raise
    except Exception as exc:
        log.warning("run %s: sign-off seeds failed: %s", ctx.run_id, exc)


async def _wave(st: RunState, cand: Cand, parts: _Parts) -> None:
    assert cand.rtl_obj is not None
    parts.wave = await waves.capture(st, cand.rtl_obj, None)


async def _strength(st: RunState, cand: Cand, parts: _Parts) -> None:
    ctx, pack = st.ctx, st.ctx.pack
    cfg = ctx.settings.signoff
    assert cand.rtl_obj is not None
    if cfg.max_mutants <= 0:
        parts.strength = _strength_out(0, 0, 0, 0, "skipped", "Planting bugs is turned off.")
        return
    mutants = plant(cand.rtl_obj.text, pack, limit=cfg.max_mutants, max_bytes=ctx.settings.rtl.max_bytes)
    if not mutants:
        parts.strength = _strength_out(0, 0, 0, 0, "skipped", "No place to plant a bug was found in this design.")
        return
    try:
        with ctx.busy():
            result = await ctx.tools.test_strength(cand.rtl_obj, pack, mutants)
    except (asyncio.CancelledError, RunStopped):
        raise
    except Exception as exc:
        log.warning("run %s: sign-off planted bugs failed: %s", ctx.run_id, exc)
        parts.strength = _strength_out(0, 0, 0, 0, "failed", "The planted-bug run did not finish (sandbox error).")
        return
    if result.status != "ok":
        reason = "it ran out of time" if result.status == "timeout" else "sandbox error"
        parts.strength = _strength_out(0, 0, 0, 0, "failed", f"The planted-bug run did not finish ({reason}).")
        return

    status_of = {row.id: row for row in result.mutants}
    rows = [(m, status_of.get(m.id)) for m in mutants]
    caught = [m for m, row in rows if row is not None and row.status == "caught"]
    survivors = [m for m, row in rows if row is not None and row.status == "survived"]
    invalid = sum(1 for _, row in rows if row is not None and row.status == "invalid")
    not_run = sum(1 for _, row in rows if row is None or row.status == "not_run")

    proofs: dict[str, EquivalenceResult] = {}
    to_prove = survivors[: cfg.max_survivor_proofs]
    proof_skip = ""
    if not survivors:
        pass
    elif not pack.eqy.enabled:
        proof_skip = "equivalence checking is turned off for this task"
    elif cand.eqy_timed_out:
        proof_skip = "equivalence timed out on this design size"
    elif to_prove:
        results = await asyncio.gather(*(_prove_survivor(st, cand, m) for m in to_prove))
        proofs = {m.id: r for m, r in zip(to_prove, results)}
    harmless = sum(1 for r in proofs.values() if r.status == "proven")
    missed = sum(1 for r in proofs.values() if r.status == "not_equivalent")
    undecided = len(survivors) - harmless - missed
    planted = len(caught) + len(survivors)

    parts.mutant_rows = [
        {
            "id": m.id,
            "rule": m.rule,
            "line": m.line,
            "before": m.before,
            "after": m.after,
            "result": row.status if row is not None else "not_run",
            "detail": row.detail if row is not None else "",
            "eqy": proofs[m.id].status if m.id in proofs else None,
        }
        for m, row in rows
    ]
    note = _strength_note(
        planted=planted,
        survivors=len(survivors),
        harmless=harmless,
        missed=missed,
        inconclusive=len(proofs) - harmless - missed,
        unchecked=len(survivors) - len(proofs),
        proof_limit=cfg.max_survivor_proofs,
        proof_skip=proof_skip,
        invalid=invalid,
        not_run=not_run,
    )
    parts.strength = _strength_out(
        planted, len(caught), harmless, undecided, "ok" if planted else "skipped", note or None
    )


async def _prove_survivor(st: RunState, cand: Cand, mutant: Mutant) -> EquivalenceResult:
    """Reset-aware EQY of the winner (gold) against a bug the tests did not catch (gate)."""
    try:
        rtl = st.ctx.pack.validate_rtl(mutant.text, max_bytes=st.ctx.settings.rtl.max_bytes)
    except Exception as exc:
        return EquivalenceResult(status="error", message=str(exc))
    assert cand.rtl_obj is not None
    return await evolve.run_eqy(st.ctx, cand.rtl_obj, rtl)


def _strength_out(planted: int, caught: int, harmless: int, undecided: int, status: str, note: str | None) -> SignoffStrength:
    return SignoffStrength(
        planted=planted, caught=caught, harmless=harmless, undecided=undecided, status=status, note=note  # type: ignore[arg-type]
    )


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _strength_note(
    *,
    planted: int,
    survivors: int,
    harmless: int,
    missed: int,
    inconclusive: int,
    unchecked: int,
    proof_limit: int,
    proof_skip: str,
    invalid: int,
    not_run: int,
) -> str:
    """Plain words for the strength numbers. Every count is from this run."""
    parts: list[str] = []
    if planted == 0:
        parts.append("No planted bug could be run, so none was counted.")
    elif survivors == 0:
        parts.append("The tests caught every planted bug.")
    else:
        parts.append(f"{_plural(survivors, 'planted bug', 'planted bugs')} passed the tests.")
        if harmless:
            parts.append(f"{_plural(harmless, 'was', 'were')} proven to behave exactly like the winner (harmless).")
        if missed:
            parts.append(f"{_plural(missed, 'was', 'were')} proven different: the tests missed {'it' if missed == 1 else 'them'}.")
        if inconclusive:
            parts.append(f"{_plural(inconclusive, 'could', 'could')} not be decided by the proof.")
        if proof_skip:
            parts.append(f"No proof was tried: {proof_skip}.")
        elif unchecked:
            parts.append(f"{_plural(unchecked, 'was', 'were')} not checked (the proof checks at most {proof_limit}).")
    if invalid:
        parts.append(f"{_plural(invalid, 'planted bug', 'planted bugs')} did not compile and {'is' if invalid == 1 else 'are'} not counted.")
    if not_run:
        parts.append(f"{_plural(not_run, 'planted bug was', 'planted bugs were')} not run (time budget).")
    return " ".join(parts)


# ======================================================================
# the report
# ======================================================================
def _report(st: RunState, pending: Pending, human_eqy: Equivalence | None, *, timed_out: bool) -> Signoff:
    ctx, pack, cand, parts = st.ctx, st.ctx.pack, pending.cand, pending.parts
    tallies: list[SeedTally] = []
    if cand.verification is not None:
        tallies.append(SeedTally(seed=pack.sim.seed, passed=cand.verification.passed, total=cand.verification.total))
    seeds = parts.seeds
    if seeds is not None:
        tallies += [SeedTally(seed=r.seed, passed=r.passed, total=r.total) for r in seeds.seeds]
    seeds_passed = sum(1 for t in tallies if t.total == pack.test_count and t.passed == t.total)

    if seeds is not None and seeds.lint is not None:
        lint = SignoffLint(
            errors=seeds.lint.errors,
            warnings=seeds.lint.warnings,
            by_tag=dict(seeds.lint.by_tag),
            latches=seeds.lint.latches if seeds.lint.latches is not None else seeds.lint.by_tag.get("LATCH", 0),
        )
    else:
        lint = _lint_from_log(cand.verification.lint_log if cand.verification else None)

    strength = parts.strength
    if strength is None:
        if pending.skipped:
            strength = _strength_out(0, 0, 0, 0, "skipped", pending.skipped)
        else:
            why = "Did not finish within the time limit." if timed_out else "Did not finish."
            strength = _strength_out(0, 0, 0, 0, "failed", why)

    base = st.baseline
    eqy_v1: EqyVsV1
    if base is not None and cand.id == base.id:
        eqy_v1 = "v1_is_winner"
    else:
        eqy_v1 = cand.eqy.status if cand.eqy is not None else "not_run"
    eqy_human: EqyVsHuman = human_eqy.status if human_eqy is not None else "not_run"
    return Signoff(
        candidate_id=cand.id,
        tests=SignoffTests(seeds_run=len(tallies), seeds_passed=seeds_passed, per_seed=tallies),
        lint=lint,
        eqy_vs_v1=eqy_v1,
        eqy_vs_human=eqy_human,
        strength=strength,
        duration_ms=int((time.monotonic() - pending.started) * 1000),
    )


def _lint_from_log(log_text: str | None) -> SignoffLint:
    """Warnings by tag from the winner's own lint log (older images, or when the seeds call did not run). The log
    of a passing design has no errors."""
    by_tag: dict[str, int] = {}
    for tag in _WARNING_TAG.findall(log_text or ""):
        by_tag[tag] = by_tag.get(tag, 0) + 1
    return SignoffLint(errors=0, warnings=sum(by_tag.values()), by_tag=by_tag, latches=by_tag.get("LATCH", 0))


def _caption(st: RunState, cand: Cand, report: Signoff) -> str:
    strength = report.strength
    counted = strength is not None and strength.status == "ok" and strength.planted > 0
    return cap.signoff(
        cand.label,
        st.ctx.pack.test_count,
        report.tests.seeds_passed,
        report.tests.seeds_run,
        report.lint.errors,
        report.lint.warnings,
        strength.caught if counted and strength else None,
        strength.planted if counted and strength else None,
    )
