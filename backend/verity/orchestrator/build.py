"""Build one candidate: validate, lint, simulate, repair (bounded), then synthesize (CONTRACTS 6.2 steps 7, 8, 10).

`evaluate` and `measure` serve both the baseline (v1, with per-stage events) and the evolve children (only
candidate events and captions, no stage events, CONTRACTS 6.2 step 10). Every branch is an `if` on tool output;
no model text decides anything.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from ..contracts import Attempt, AttemptBy, Verification, to_json
from ..core import captions as cap
from ..core.errors import RunStopped
from ..core.results import WaveTrace
from ..core.rtl_fixer import failure_signature, fix_declarations, fixer_applies, lint_issues, normalize_comment_chars
from ..core.types import (
    AttemptMeta,
    BudgetExhausted,
    CallMeta,
    MeasureResult,
    RtlOut,
    RtlRejected,
    Rtl,
    RunContext,
    VerifyResult,
)
from . import judge, waves
from .candidates import FIRST_VERSION_REASON, Cand
from .state import RunState, meta_dict

log = logging.getLogger("verity.orchestrator")

FIXER_MODEL = "verity-declaration-fixer"  # CallMeta.model of a deterministic fix (no LLM call was made)
MAX_AUTO_FIXES = 3  # deterministic fixes per candidate; each one is idempotent, so this is only a loop guard


@dataclass(slots=True)
class _Checked:
    verification: Verification | None
    passed: bool = False
    rtl: Rtl | None = None
    result: VerifyResult | None = None
    rules_problem: str | None = None  # first RtlRejected problem
    duplicate_of: str | None = None  # id of the candidate that already used this exact RTL


def _default_reason(st: RunState, c: Cand) -> str:
    if c.created_by == "INIT" or c.parent_id is None:
        return FIRST_VERSION_REASON
    parent = st.cands.get(c.parent_id)
    return f"{c.created_by.title()} version of {parent.label if parent else c.parent_id}"


def _chapter(c: Cand, baseline: bool) -> str:
    return cap.CH_BUILD if baseline else cap.round_chapter(c.round)


# ======================================================================
# R4: k parallel first attempts for the baseline
# ======================================================================
@dataclass(slots=True)
class _Sample:
    out: RtlOut
    text: str  # the RTL after the deterministic fixer (the original text when the fixer changed nothing)
    fixes: list[str]
    lint_ok: bool
    passed: int = 0
    total: int = 0
    lint_issue_count: int = 0
    note: str = ""

    @property
    def full(self) -> bool:
        return self.lint_ok and self.total > 0 and self.passed == self.total

    @property
    def score(self) -> float:
        return self.passed / self.total if self.lint_ok and self.total else -1.0


def prior_kwargs(ctx: RunContext) -> dict[str, tuple[str, ...]]:
    """generate_rtl's prior_attempt for a run that has one (a CPU core retry), else nothing, so a role without the
    argument keeps working."""
    return {"prior_attempt": ctx.prior_attempt} if ctx.prior_attempt else {}


async def sample_baseline(st: RunState, c: Cand) -> RtlOut:
    """Write k first versions in parallel (temperature and top_p from [baseline]), run the declaration fixer and the
    checks on each, drop the ones that still fail lint, and hand back the one that passes the most tests.

    The winner goes through `evaluate` as attempt 1, which re-runs the checks once (a sandbox call, no model call)
    and repairs it if it is not a full pass. Nothing about the other samples reaches the events or the candidate.
    """
    ctx, pack = st.ctx, st.ctx.pack
    cfg = ctx.settings.baseline
    assert st.plan is not None

    async def one() -> RtlOut:
        with ctx.busy():
            return await ctx.roles.generate_rtl(
                ctx.llm, pack, st.plan, ctx.priority, temperature=cfg.temperature, top_p=cfg.top_p, **prior_kwargs(ctx)
            )

    results = await asyncio.gather(*(one() for _ in range(cfg.samples)), return_exceptions=True)
    await st.credits()
    for r in results:  # a stop or cancel always wins over a partial result
        if isinstance(r, (asyncio.CancelledError, RunStopped)):
            raise r
    outs = [r for r in results if isinstance(r, RtlOut)]
    if not outs:
        raise next(r for r in results if isinstance(r, BaseException))
    for r in results:
        if isinstance(r, BudgetExhausted):
            st.note_budget(r.limit)

    samples = list(await asyncio.gather(*(_score_sample(st, out) for out in outs)))
    best = _pick_sample(samples)
    chosen = samples.index(best)
    await st.save_json(
        "candidates/c1/samples.json",
        {
            "requested": cfg.samples,
            "returned": len(outs),
            "chosen": chosen,
            "samples": [
                {
                    "index": i,
                    "lintOk": s.lint_ok,
                    "passed": s.passed,
                    "total": s.total,
                    "fixerChanges": s.fixes,
                    "note": s.note,
                    "outputTokens": s.out.meta.output_tokens,
                    "reasoningTokens": s.out.meta.reasoning_tokens,
                    "finishReason": s.out.meta.finish_reason,
                    "rtlSha256": hashlib.sha256(s.text.encode("utf-8", "replace")).hexdigest(),
                }
                for i, s in enumerate(samples)
            ],
        },
    )
    c.auto_fixes += len(best.fixes)
    await st.em.caption(cap.sampled(len(outs), best.passed, best.total), cap.CH_BUILD)
    return replace(best.out, text=best.text)


async def _score_sample(st: RunState, out: RtlOut) -> _Sample:
    """Fixer plus one verify per round on a single sample. Never touches the candidate or emits events."""
    ctx, pack = st.ctx, st.ctx.pack
    text, fixes = normalize_comment_chars(out.text), []
    while True:
        try:
            rtl = pack.validate_rtl(text, max_bytes=ctx.settings.rtl.max_bytes)
        except RtlRejected as exc:
            return _Sample(out, text, fixes, False, total=pack.test_count, note=(exc.problems or ["invalid RTL"])[0])
        with ctx.busy():
            result = await ctx.tools.verify(rtl, pack)
        verification = result.to_verification(pack.test_count, timeout_s=ctx.settings.timeouts.sim_s)
        if verification.lint == "fail" and fixer_applies(verification.lint_log) and len(fixes) < MAX_AUTO_FIXES:
            fixed = fix_declarations(text)
            if fixed.changed:
                text, fixes = fixed.text, fixes + [f"{'; '.join(fixed.changes)}"]
                continue
        lint_ok = verification.lint == "pass"
        return _Sample(
            out,
            text,
            fixes,
            lint_ok,
            passed=verification.passed if lint_ok else 0,
            total=verification.total,
            lint_issue_count=0 if lint_ok else len(lint_issues(verification.lint_log)),
            note="" if lint_ok else "lint failed",
        )


def _pick_sample(samples: list[_Sample]) -> _Sample:
    """Highest fraction of tests passed among samples that pass lint (the first one on a tie). If none passes lint,
    the one with the fewest lint issues, so the repair loop starts from the smallest problem."""
    alive = [s for s in samples if s.lint_ok]
    if alive:
        return max(alive, key=lambda s: (s.score, -samples.index(s)))
    return min(samples, key=lambda s: (s.lint_issue_count if s.lint_issue_count else 10**6, samples.index(s)))


# ======================================================================
# verify loop
# ======================================================================
async def evaluate(st: RunState, c: Cand, out: RtlOut, *, baseline: bool) -> bool:
    """Run attempt 1 (`out`) and then FIX attempts until the candidate passes or its repair budget is spent.

    Returns True when all tests pass (c.status "pass"). On failure the baseline keeps status "fail" (the caller ends
    the run) and a child becomes "rejected". Raises RunStopped / CancelledError, LLMError, RoleOutputError and
    ToolInfraError to the caller.
    """
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    limits = ctx.ledger.limits
    max_fixes = limits.max_repairs_baseline if baseline else limits.max_repairs_child
    chapter = _chapter(c, baseline)
    by: AttemptBy = c.created_by
    c.reason = out.reason.strip() or _default_reason(st, c)

    while True:
        ctx.check_stop()
        n = len(c.attempts) + 1
        c.rtl = out.text
        if out.meta.model != FIXER_MODEL:
            await st.record_meta(out.meta)

        checked = await _check_attempt(st, c, out.text, baseline=baseline)

        if checked.duplicate_of is not None:
            same_as = st.cands[checked.duplicate_of]
            c.reject()
            await em.candidate(c)
            await em.caption(cap.child_duplicate(c.label, same_as.label), chapter)
            return False

        assert checked.verification is not None
        verification = checked.verification
        attempt = Attempt(
            n=n,
            by=by,
            reason=out.reason.strip() or (FIRST_VERSION_REASON if n == 1 else "Fix attempt"),
            rtl=checked.rtl.text if checked.rtl is not None else out.text,
            verification=verification,
        )
        c.attempts.append(attempt)
        c.verification = verification
        c.rtl = attempt.rtl
        attempt_meta = await _persist_attempt(st, c, attempt, out, checked)

        if checked.passed and checked.rtl is not None:
            c.status = "pass"
            c.rtl_obj = checked.rtl
            if baseline:
                c.is_baseline = True
                await em.stage("sim", "done")
                if c.fix_count:
                    await em.stage("fix", "done", detail=f"{c.fix_count} fix" + ("" if c.fix_count == 1 else "es"))
            await em.candidate(c)
            if baseline:
                await em.caption(cap.baseline_passes(c.label, pack.test_count), chapter)
            return True

        # ---------- failed ----------
        c.status = "fail"
        await em.candidate(c)
        if baseline and checked.rules_problem is None and verification.lint == "pass":
            await em.stage("sim", "failed")

        # R5: the same failure signature twice for one candidate means patching is not working.
        signature = _signature(verification)
        c.signatures.append(signature)
        stalled = c.signatures.count(signature) >= 2

        # R2: a lint failure with a fixable tag first gets the deterministic declaration fixer, with no LLM call and no repair spent.
        if verification.lint == "fail" and fixer_applies(verification.lint_log) and c.auto_fixes < MAX_AUTO_FIXES and not ctx.stopped:
            fixed = await _auto_fix(st, c, attempt, attempt_meta, baseline=baseline)
            if fixed is not None:
                out, by = fixed, "FIX"
                continue

        can_fix = c.fix_count < max_fixes and not ctx.stopped
        if can_fix and st.budget_blocked():
            can_fix = False
        restart = can_fix and stalled and baseline and st.plan is not None
        if can_fix and stalled and not restart:
            can_fix = False  # a stalled child is discarded; regenerating it from the plan would just recreate v1
        if baseline or can_fix:
            await em.caption(_fail_caption(c, verification, checked, baseline), chapter)
        if can_fix:
            try:
                if restart:
                    repaired = await _restart(st, c, signature)
                else:
                    trace = await _failing_wave(st, c, checked, n)
                    repaired = await _repair(st, c, attempt.rtl, verification, n + 1, baseline=baseline, waves=trace)
            except BudgetExhausted as exc:
                st.note_budget(exc.limit)
                c.status = "fail"
                await em.candidate(c)
                repaired = None
            if repaired is not None:
                out, by = repaired, "FIX"
                # Attach the diagnosis to the failed attempt and show the fix in flight.
                await _attach_diagnosis(st, c, attempt, attempt_meta, repaired, baseline=baseline)
                c.fix_count += 1
                continue

        # No more repairs (or none allowed): baseline stops the run, a child is rejected.
        if baseline:
            if c.fix_count:
                await em.stage("fix", "failed", detail=f"{c.fix_count} fix" + ("" if c.fix_count == 1 else "es"))
            await em.caption(cap.no_baseline(c.fix_count), chapter)
        else:
            c.reject()
            await em.candidate(c)
            await em.caption(cap.child_rejected(c.label), chapter)
        return False


def _fail_caption(c: Cand, v: Verification, checked: _Checked, baseline: bool) -> str:
    if checked.rules_problem is not None:
        return cap.rejected_rules(c.label, checked.rules_problem)
    if v.lint == "fail":
        return cap.failed_lint(c.label)
    failed = max(1, v.total - v.passed)
    return cap.failed_tests(c.label, failed, v.total) if baseline else cap.child_failed(c.label, failed, v.total)


async def _check_attempt(st: RunState, c: Cand, text: str, *, baseline: bool) -> _Checked:
    """Validate the RTL, then (if it is new) run one sandbox verify call and map it to a Verification."""
    ctx, em, pack = st.ctx, st.em, st.ctx.pack

    try:
        rtl = pack.validate_rtl(normalize_comment_chars(text), max_bytes=ctx.settings.rtl.max_bytes)
    except RtlRejected as exc:
        problems = exc.problems
        verification = Verification(
            lint="fail",
            lint_log="Rejected before simulation:\n" + "\n".join(f"- {p}" for p in problems),
            passed=0,
            total=pack.test_count,
            failures=[],
            tool="Verity RTL rules",
            duration_ms=0,
        )
        if baseline:
            await em.stage("lint", "failed")
        return _Checked(verification, rules_problem=problems[0] if problems else "invalid RTL")

    owner = st.sha_owner.get(rtl.sha256)
    if owner is not None and owner != c.id:
        return _Checked(None, rtl=rtl, duplicate_of=owner)
    if owner == c.id and c.verification is not None:
        # A "fix" that returned the same RTL again: no sandbox call, it fails exactly like before.
        return _Checked(c.verification, rtl=rtl)
    st.sha_owner[rtl.sha256] = c.id

    if baseline:
        await em.stage("lint", "active")
    c.status = "linting"
    await em.candidate(c)

    with ctx.busy():
        result = await ctx.tools.verify(rtl, pack)
    verification = result.to_verification(pack.test_count, timeout_s=ctx.settings.timeouts.sim_s)
    passed = result.passed_all(pack.test_count)

    if verification.lint == "pass":
        if baseline:
            await em.stage("lint", "done")
            await em.stage("sim", "active")
        c.status = "simulating"
        await em.candidate(c)
        if baseline:
            await em.caption(cap.simulating(pack.test_count), cap.CH_BUILD)
    elif baseline:
        await em.stage("lint", "failed")
    return _Checked(verification, passed=passed, rtl=rtl, result=result)


async def _persist_attempt(st: RunState, c: Cand, attempt: Attempt, out: RtlOut, checked: _Checked) -> AttemptMeta:
    ctx = st.ctx
    sha = checked.rtl.sha256 if checked.rtl is not None else hashlib.sha256(out.text.encode("utf-8", "replace")).hexdigest()
    base = f"candidates/{c.id}/attempt-{attempt.n}"
    meta = AttemptMeta(
        model_id=out.meta.model,
        prompt_version=out.meta.prompt_version,
        llm_request_id=out.meta.request_id,
        input_tokens=out.meta.input_tokens,
        output_tokens=out.meta.output_tokens,
        rtl_sha256=sha,
        rtl_artifact=f"{base}.v",
        tool_image_digest=st.image_digest or None,
        created_at=datetime.now(UTC),
    )
    try:
        await ctx.store.add_attempt(ctx.run_id, c.id, attempt, meta)
    except Exception:
        # Store trouble must not kill a run whose events are already streaming.
        log.exception("run %s: add_attempt failed", ctx.run_id)
    await st.save(f"{base}.v", attempt.rtl, "text/x-verilog")
    if checked.result is not None:
        await st.save(f"{base}.verify.json", checked.result.model_dump_json(by_alias=True, indent=2), "application/json")
    else:
        await st.save(f"{base}.verify.json", to_json(attempt.verification), "application/json")
    await st.save(
        f"{base}.llm.json", json.dumps({"raw": out.raw, "meta": meta_dict(out.meta)}, indent=2), "application/json"
    )
    return meta


def _signature(v: Verification) -> str:
    failing = next((case.name for case in v.failures if not case.pass_), None)
    return failure_signature(v.lint, v.lint_log, failing)


async def _auto_fix(
    st: RunState, c: Cand, attempt: Attempt, meta: AttemptMeta, *, baseline: bool
) -> RtlOut | None:
    """R2: run the deterministic declaration fixer on a lint failure. None when it changes nothing.

    The result goes back through the normal check as a new attempt (by FIX). It spends no LLM call and no repair.
    """
    result = fix_declarations(attempt.rtl)
    if not result.changed:
        return None
    c.auto_fixes += 1
    summary = "Declaration fix without a model call: " + "; ".join(result.changes) + "."
    issues = [f"{i.tag}:{i.signal}" if i.signal else i.tag for i in lint_issues(attempt.verification.lint_log)]
    await st.save_json(
        f"candidates/{c.id}/attempt-{attempt.n}.autofix.json",
        {"fromAttempt": attempt.n, "lintIssues": issues, "changes": result.changes},
    )
    await st.em.caption(cap.auto_fixed(c.label, "; ".join(result.changes)), _chapter(c, baseline))
    fixer_meta = CallMeta(
        role="repair", model=FIXER_MODEL, request_id="", prompt_version="rtl_fixer@1",
        input_tokens=0, output_tokens=0, latency_ms=0, usd=0.0,
    )
    out = RtlOut(text=result.text, reason=cap.clean(summary, 200), meta=fixer_meta, raw="", diagnosis=summary)
    await _attach_diagnosis(st, c, attempt, meta, out, baseline=baseline)
    return out


async def _restart(st: RunState, c: Cand, signature: str) -> RtlOut:
    """R5: discard the stalled RTL and regenerate from the approved plan. Counts as one repair and one LLM call."""
    ctx, em = st.ctx, st.em
    assert st.plan is not None
    c.status = "fixing"
    await em.candidate(c)
    await em.caption(cap.restarted(c.label), cap.CH_BUILD)
    await st.save_json(f"candidates/{c.id}/stall-{len(c.attempts)}.json", {"signature": signature})
    with ctx.busy():
        fresh = await ctx.roles.generate_rtl(ctx.llm, ctx.pack, st.plan, ctx.priority)
    ctx.ledger.count("repair")
    await st.credits()
    c.signatures.clear()  # the new version gets a fresh two-strikes count
    return replace(
        fresh,
        reason="Restarted from the plan after the same failure twice",
        diagnosis=f"The same failure came back twice ({cap.clean(signature, 120)}), so the version was rewritten from the plan.",
    )


async def _repair(
    st: RunState,
    c: Cand,
    rtl_text: str,
    verification: Verification,
    attempt_n: int,
    *,
    baseline: bool,
    waves: WaveTrace | None = None,
) -> RtlOut | None:
    ctx, em = st.ctx, st.em
    if baseline:
        await em.stage("fix", "active")
    c.status = "fixing"
    await em.candidate(c)
    extra: dict[str, object] = {"waves": waves} if waves is not None else {}
    parent = st.cands.get(c.parent_id) if not baseline and c.parent_id else None
    if parent is not None and parent.rtl_obj is not None:
        extra["parent_rtl"] = parent.rtl_obj.text  # a failing child sees the parent that passes every test
    with ctx.busy():
        repaired = await ctx.roles.repair(ctx.llm, ctx.pack, rtl_text, verification, attempt_n=attempt_n, **extra)
    ctx.ledger.count("repair")
    await st.credits()
    return repaired


async def _failing_wave(st: RunState, c: Cand, checked: _Checked, attempt_n: int) -> WaveTrace | None:
    """The waveform of the first failing test case of a version that failed its tests, for its repair prompt and the
    waves/<id>.json artifact. None for lint, rule or interface failures, or when the waveform cannot be made."""
    ctx = st.ctx
    result = checked.result
    if not ctx.settings.signoff.waves_on_repair or checked.rtl is None or result is None or result.sim is None:
        return None
    if result.status != "sim_fail" or not result.sim.failures:
        return None
    ctx.check_stop()
    trace = await waves.capture(st, checked.rtl, result.sim.failures[0].name)
    if trace is not None:
        await waves.publish(st, c, trace, file_name=f"attempt-{attempt_n}.waves.json")
    return trace


async def _attach_diagnosis(
    st: RunState, c: Cand, attempt: Attempt, meta: AttemptMeta, repaired: RtlOut, *, baseline: bool
) -> None:
    """Put the repair role's diagnosis on the attempt it was about, and re-upsert (CONTRACTS 6.2 step 7)."""
    if not repaired.diagnosis:
        return
    text = cap.clean(repaired.diagnosis, 300)
    if not text:
        return
    verification = attempt.verification.model_copy(update={"diagnosis": text})
    updated = attempt.model_copy(update={"verification": verification})
    c.attempts[-1] = updated
    c.verification = verification
    try:
        await st.ctx.store.add_attempt(st.ctx.run_id, c.id, updated, meta)
    except Exception:
        log.exception("run %s: could not store the diagnosis", st.ctx.run_id)
    await st.em.candidate(c)
    if baseline:
        await st.em.caption(cap.diagnosis(text), cap.CH_BUILD)


# ======================================================================
# measure
# ======================================================================
async def measure(st: RunState, c: Cand, *, baseline: bool) -> bool:
    """Synthesize a passing candidate (Yosys + OpenSTA). True when it is measured and ranked.

    Baseline failure leaves the status "synthesizing" (the caller ends the run). A child that does not synthesize is
    rejected.
    """
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    assert c.rtl_obj is not None, "measure() needs a passing candidate"
    chapter = _chapter(c, baseline)

    ctx.check_stop()
    if baseline:
        await em.stage("synth", "active")
    c.status = "synthesizing"
    await em.candidate(c)

    with ctx.busy():
        result = await ctx.tools.measure(c.rtl_obj, pack)
    c.measure = result

    if not result.measured:
        await st.save_json(f"candidates/{c.id}/synth-failure.json", {"status": result.status, "message": result.message})
        if baseline:
            await em.stage("synth", "failed")
            await em.caption(cap.baseline_synth_failed(c.label), chapter)
        else:
            c.reject()
            await em.candidate(c)
            await em.caption(cap.child_synth_failed(c.label), chapter)
        return False

    ppa = result.to_ppa()
    c.ppa = ppa
    if baseline:
        c.fitness = judge.baseline_fitness(ppa, ctx.priority, c.id)
    else:
        base = st.baseline
        assert base is not None and base.ppa is not None
        c.fitness = judge.candidate_fitness(base.ppa, ppa, ctx.priority, base.id)
    c.status = "measured"
    await em.candidate(c)
    if baseline:
        await em.stage("synth", "done")
    return True


async def publish_reports(st: RunState, c: Cand) -> None:
    """Artifact events and files for a measured candidate: synth report, STA report, circuit graph."""
    em = st.em
    result = c.measure
    if result is None or c.ppa is None:
        return
    synth_text = _synth_report(result)
    await em.artifact(f"reports/synth_{c.label}.txt", synth_text)
    await st.save(f"candidates/{c.id}/synth.log", synth_text)
    if result.sta is not None:
        sta_text = _sta_report(result)
        await em.artifact(f"reports/sta_{c.label}.txt", sta_text)
        await st.save(f"candidates/{c.id}/sta.log", sta_text)
    circuit = result.circuit_for(c.id)
    if circuit is not None:
        body = to_json(circuit)
        await em.artifact(f"circuit/{c.id}.json", body)
        await st.save(f"candidates/{c.id}/circuit.json", body, "application/json")
    await st.save(f"candidates/{c.id}/ppa.json", to_json(c.ppa), "application/json")


def _synth_report(result: MeasureResult) -> str:
    synth = result.synth
    lines = [
        f"Tool: {synth.tool if synth else 'Yosys'}",
        f"Library: {result.pdk}, post-synthesis estimate (no wires)",
    ]
    if synth is not None:
        if synth.area_um2 is not None:
            lines.append(f"Area: {synth.area_um2:.2f} um2")
        if synth.cell_count is not None:
            lines.append(f"Cells: {synth.cell_count}")
        for name, count in sorted(synth.cells.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {name}: {count}")
        if synth.log:
            lines += ["", "--- log (tail) ---", synth.log]
    return "\n".join(lines) + "\n"


def _sta_report(result: MeasureResult) -> str:
    sta = result.sta
    assert sta is not None
    lines = [f"Tool: {sta.tool}", f"Library: {result.pdk}, post-synthesis timing estimate (no wires)"]
    if sta.ok and sta.clock_period_ns is not None:
        kind = "clock" if sta.clocked else "virtual clock (combinational design)"
        lines.append(f"Minimum clock period: {sta.clock_period_ns:.3f} ns ({kind})")
        if sta.target_period_ns is not None and sta.wns_ns is not None:
            lines.append(f"Target period {sta.target_period_ns:.3f} ns, worst setup slack {sta.wns_ns:.3f} ns")
    else:
        lines.append("Timing was not measured.")
    power = result.power
    if power is not None and power.ok and power.power_mw is not None:
        lines.append(
            f"Power estimate: {power.power_mw:.4f} mW at {power.reference_clock_ns} ns reference clock "
            f"({power.method}, input activity {power.input_activity}; internal {power.internal_mw:.4f}, "
            f"switching {power.switching_mw:.4f}, leakage {power.leakage_mw:.4f} mW; not signoff)"
        )
    if sta.log:
        lines += ["", "--- log (tail) ---", sta.log]
    return "\n".join(lines) + "\n"
