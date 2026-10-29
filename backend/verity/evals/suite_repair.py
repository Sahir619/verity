"""`repair` suite: known-good reference with an injected bug, repaired within N attempts. Costs LLM credits with nebius.

Per task, injector and repeat: inject a bug into reference.v, confirm the locked tests catch it (otherwise the row is a
`bug_not_detected` equivalent mutant and does not count), then call the repair role up to `max_repairs_baseline` times,
verifying each attempt in the sandbox. `initial_pass` is False for every counted row (the bug fails), `final_pass` and
`repair_success` say whether the loop fixed it, `repairs_used` how many attempts it took. As in a live run, a test
failure gets a waveform of its first failing case before the repair call (the cycle table of repair.v3);
`extra.cycle_tables` counts the repair calls that had one.
"""

import logging

from ..contracts import Verification
from ..core.errors import BudgetExhausted, LLMError, RoleOutputError, RtlRejected, ToolInfraError
from ..core.pack import TaskPack
from ..core.results import VerifyResult, WaveTrace
from ..core.rtl import Rtl
from ..roles import default_roles
from .harness import EvalEnv
from .injectors import INJECTORS, inject
from .record import EvalRecord

log = logging.getLogger("verity.evals")


async def run(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
    if env.skip_sandbox:
        log.warning("the repair suite needs the sandbox: skipped (--skip-sandbox)")
        return
    for pack in packs:
        reference = pack.reference_rtl()
        if reference is None:
            record = env.new_record("repair", pack.id)
            record.note = "no reference.v"
            record.error = "skipped"
            await env.write(record)
            continue
        for case in INJECTORS:
            for repeat in range(1, repeats + 1):
                if not env.budget_left():
                    log.warning("eval budget reached, stopping the repair suite")
                    return
                record = await _one(env, pack, reference, case, repeat)
                await env.write(record)


async def _wave(env: EvalEnv, record: EvalRecord, rtl: Rtl, pack: TaskPack, result: VerifyResult) -> WaveTrace | None:
    """The waveform the orchestrator would give the next repair (build._failing_wave), or None."""
    if not env.settings.signoff.waves_on_repair or result.status != "sim_fail" or result.sim is None or not result.sim.failures:
        return None
    try:
        with record.stage("wave"):
            wave = await env.tools.waveform(rtl, pack, result.sim.failures[0].name)
    except ToolInfraError as exc:
        log.warning("waveform failed: %s", exc)
        return None
    return wave.wave if wave.status == "ok" else None


def _rules_failure(problems: list[str], test_count: int) -> Verification:
    return Verification(
        lint="fail",
        lint_log="Rejected before simulation:\n" + "\n".join(f"- {p}" for p in problems),
        passed=0,
        total=test_count,
        failures=[],
        tool="Verity RTL rules",
        duration_ms=0,
    )


async def _one(env: EvalEnv, pack: TaskPack, reference: str, case: str, repeat: int) -> EvalRecord:
    record = env.new_record("repair", pack.id, repeat, case)
    injection = inject(case, reference, pack)
    if injection is None:
        record.note = "not_applicable: the injector found nothing to change in this design"
        record.extra["counted"] = False
        return record
    record.extra["injection"] = injection.description

    try:
        buggy = pack.validate_rtl(injection.text, max_bytes=env.settings.rtl.max_bytes)
    except RtlRejected as exc:
        record.note = "injection_rejected_by_rules: " + "; ".join(exc.problems)[:200]
        record.extra["counted"] = False
        return record

    roles = default_roles()
    llm, ledger = env.new_llm(pack)
    max_fixes = ledger.limits.max_repairs_baseline
    versions: dict[str, str] = {}
    try:
        with record.stage("sim"):
            detected = await env.tools.verify(buggy, pack)
        if detected.passed_all(pack.test_count):
            record.note = "bug_not_detected: the locked tests pass on the buggy design (equivalent mutant)"
            record.extra["counted"] = False
            return record
        record.extra["counted"] = True
        record.extra["detected_by"] = detected.status
        record.initial_pass = False
        record.repairs_used = 0
        verification = detected.to_verification(pack.test_count, timeout_s=env.settings.timeouts.sim_s)
        current = buggy.text
        record.final_pass = False
        record.repair_success = False
        trace = await _wave(env, record, buggy, pack, detected)
        record.extra["cycle_tables"] = 0

        for n in range(1, max_fixes + 1):
            extra = {"waves": trace} if trace is not None else {}
            record.extra["cycle_tables"] += 1 if trace is not None else 0
            with record.stage("fix"):
                out = await roles.repair(llm, pack, current, verification, attempt_n=n + 1, **extra)
            versions[out.meta.role] = out.meta.prompt_version
            record.extra.setdefault("diagnoses", []).append(out.diagnosis)
            record.repairs_used = n
            try:
                rtl = pack.validate_rtl(out.text, max_bytes=env.settings.rtl.max_bytes)
            except RtlRejected as exc:
                verification = _rules_failure(exc.problems, pack.test_count)
                current = out.text
                trace = None
                continue
            with record.stage("sim"):
                result = await env.tools.verify(rtl, pack)
            if result.passed_all(pack.test_count):
                record.final_pass = True
                record.repair_success = True
                break
            verification = result.to_verification(pack.test_count, timeout_s=env.settings.timeouts.sim_s)
            current = rtl.text
            trace = await _wave(env, record, rtl, pack, result)
    except (BudgetExhausted, LLMError, RoleOutputError, ToolInfraError) as exc:
        record.error = f"{type(exc).__name__}: {exc}"
    finally:
        snap = ledger.snapshot()
        record.llm_calls = snap.llm_calls
        record.input_tokens = snap.input_tokens
        record.output_tokens = snap.output_tokens
        record.est_usd = round(snap.usd, 6)
        record.prompt_versions = versions
    return record
