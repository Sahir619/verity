"""Repair role (operator FIX): failing RTL plus its lint log or up to 5 failing cases in, corrected RTL out.

The prompt contains the spec brief, the current RTL and the failure evidence. It never contains golden.py,
test_dut.py or reference.v. Expected values shown for failing cases come from the Verification record. When the
orchestrator has a waveform of the first failing case (/verity/bin/wave), a compact cycle table around the first
mismatch follows the cases: ports and a few registers, got and expected (the golden model replayed on the same inputs).
"""

import hashlib

from ..contracts import TestCase, Verification
from ..core.llm_types import RtlOut
from ..core.pack import TaskPack
from ..core.protocols import LLMGateway
from ..core.results import WaveTrace
from ..core.rtl_fixer import guidance_block
from .common import call_parsed
from .parsing import RtlAnswer, parse_rtl_answer
from .prompts import load_template

TEMPLATE_FILE = "repair.v4.md"
FORMAT_HINT = "Give one DIAGNOSIS line, one REASON line, then exactly one ```verilog fenced block with the whole file."
MAX_LINT_CHARS = 4_000
MAX_CASES = 5
MAX_TABLE_CHARS = 2_500
NOT_CHECKED = "."
DEFAULT_REASON = "Fixed the reported failure"
DEFAULT_DIAGNOSIS = "The reported failure was addressed in the new version."
PARENT_NOTE = (
    "The parent passes every test. The bug is in what this version changed. Keep the improvement if you can; "
    "otherwise restore the parent's logic for the broken part."
)


async def repair(
    llm: LLMGateway,
    pack: TaskPack,
    rtl_text: str,
    verification: Verification,
    *,
    attempt_n: int,
    waves: WaveTrace | None = None,
    parent_rtl: str | None = None,
) -> RtlOut:
    """`parent_rtl`: the passing parent of a failing child version (None for v1). It is shown after the evidence."""
    template = load_template(TEMPLATE_FILE)
    system, user = template.render(
        task_brief=pack.public_brief(),
        current_rtl=rtl_text.strip("\n"),
        failure=_failure_text(verification, rtl_text, waves),
        parent_block=_parent_block(parent_rtl),
        top=pack.top,
        latency_cycles=str(pack.latency_cycles),
    )
    parsed = await call_parsed(
        llm,
        "repair",
        template=template,
        system=system,
        user=user,
        parse=lambda text: parse_rtl_answer(text, pack.top, want_diagnosis=True),
        tags={
            "task_id": pack.id,
            "pack_root": str(pack.root),
            "attempt": str(attempt_n),
            "source_sha": hashlib.sha256(rtl_text.encode("utf-8", errors="replace")).hexdigest(),
        },
        format_hint=FORMAT_HINT,
    )
    answer: RtlAnswer = parsed.value
    return RtlOut(
        text=answer.text,
        reason=answer.reason or DEFAULT_REASON,
        meta=parsed.meta,
        raw=parsed.raw,
        diagnosis=answer.diagnosis or DEFAULT_DIAGNOSIS,
    )


def _parent_block(parent_rtl: str | None) -> str:
    if not parent_rtl or not parent_rtl.strip():
        return ""
    code = parent_rtl.strip("\n")
    return f"## Parent design (passes every test)\n```verilog\n{code}\n```\n\n{PARENT_NOTE}"


def _failure_text(verification: Verification, rtl_text: str = "", waves: WaveTrace | None = None) -> str:
    """Lint log (first 4,000 chars) plus matching guidance when lint failed, else at most 5 failing cases and, when
    there is one, the cycle table of the first failing case."""
    if verification.lint == "fail":
        log = (verification.lint_log or "no log was produced").strip()
        text = "The design did not pass the checks before simulation. Tool output:\n\n" + log[:MAX_LINT_CHARS]
        guidance = guidance_block(log, rtl_text)
        return text + "\n\n" + guidance if guidance else text
    failing = [case for case in verification.failures if not case.pass_][:MAX_CASES]
    lines = [
        f"Lint passed. Simulation passed {verification.passed} of {verification.total} test cases.",
        f"Showing {len(failing)} failing case{'s' if len(failing) != 1 else ''}:",
        "",
    ]
    for case in failing:
        lines.append(_case_text(case))
    if not failing:
        lines.append("(no case details were reported)")
    table = cycle_table(waves) if waves is not None and waves.fail_cycle is not None else ""
    if table:
        lines += ["", table]
    return "\n".join(lines)


def cycle_table(waves: WaveTrace) -> str:
    """One row per signal, one column per cycle, the failing cycle marked with *. Empty when there is nothing to show."""
    if not waves.signals or not waves.signals[0].got:
        return ""
    count = len(waves.signals[0].got)
    head = [f"{waves.start_cycle + i}{'*' if waves.start_cycle + i == waves.fail_cycle else ''}" for i in range(count)]
    rows: list[tuple[str, list[str]]] = [("cycle", head)]
    for sig in waves.signals:
        rows.append((sig.name, list(sig.got)))
        if sig.expected is not None:
            rows.append((f"{sig.name} expected", [value or NOT_CHECKED for value in sig.expected]))
    label_w = max(len(label) for label, _ in rows)
    col_w = [max(len(cells[i]) for _, cells in rows if i < len(cells)) for i in range(count)]
    body = [
        f"{label:<{label_w}}  " + "  ".join(f"{cell:<{col_w[i]}}" for i, cell in enumerate(cells[:count])).rstrip()
        for label, cells in rows
    ]
    lines = [
        f"Cycle table of {waves.case} around the first mismatch (cycle {waves.fail_cycle}, marked *). "
        "Rows without 'expected' are inputs or registers of your design.",
        *body,
    ]
    if waves.note:
        lines.append(waves.note)
    return "\n".join(lines)[:MAX_TABLE_CHARS]


def _case_text(case: TestCase) -> str:
    when = f" at cycle {case.cycle}" if case.cycle is not None else ""
    return f"- {case.name}{when}\n    inputs:   {case.inputs}\n    expected: {case.expected}\n    got:      {case.got}"
