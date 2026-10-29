"""RTL generator role: the approved plan in, the baseline Verilog (v1, createdBy INIT) out."""

import re
from collections.abc import Sequence

from ..contracts import Priority
from ..core.llm_types import PlanOut, RtlOut
from ..core.pack import TaskPack
from ..core.protocols import LLMGateway
from .common import call_parsed, priority_text, render_plan
from .parsing import RtlAnswer, parse_rtl_answer
from .prompts import load_template

TEMPLATE_FILE = "rtl_gen.v3.md"
# v3 plus what an earlier attempt at this same design failed (a CPU core retry). Used only when there are such lines.
PRIOR_TEMPLATE_FILE = "rtl_gen.v4.md"
FORMAT_HINT = "Give one REASON line, then exactly one ```verilog fenced block with the whole file."
DEFAULT_REASON = "First version, written from the plan"
PRIOR_HEADING = "Told to Nemotron: an earlier attempt at this same design failed these checks"  # the run's spec.md
PRIOR_MAX_LINES = 12
PRIOR_MAX_CHARS = 160
_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[^\s/\\:'\"`]*[/\\])+")


def ascii_line(text: object, limit: int = PRIOR_MAX_CHARS) -> str:
    """ASCII only, no file paths (a name keeps its file name and line number), one line, at most `limit` characters."""
    return " ".join(_PATH.sub("", str(text).encode("ascii", "ignore").decode("ascii")).split())[:limit].strip()


def prior_lines(lines: Sequence[str]) -> tuple[str, ...]:
    """The lines of an earlier failed attempt as Nemotron may read them: ascii_line each, each once, at most
    PRIOR_MAX_LINES. The caller passes only code-written sandbox and test output, never model or user text."""
    out: list[str] = []
    for line in lines:
        clean = ascii_line(line)
        if clean and clean not in out:
            out.append(clean)
    return tuple(out[:PRIOR_MAX_LINES])


def prior_text(lines: Sequence[str]) -> str:
    """The lines as the v4 prompt shows them, one bullet each."""
    return "\n".join(f"- {line}" for line in prior_lines(lines))


async def generate_rtl(
    llm: LLMGateway,
    pack: TaskPack,
    plan: PlanOut,
    priority: Priority,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    prior_attempt: Sequence[str] = (),
) -> RtlOut:
    """Raises RoleOutputError when no Verilog block can be extracted (after one retry). The text is NOT validated
    against the coding rules here: the orchestrator calls pack.validate_rtl() so a rejection is a normal attempt.
    `prior_attempt`: what an earlier attempt at this same design failed (code-written lines); with any, v4 is used."""
    told = prior_text(prior_attempt)
    template = load_template(PRIOR_TEMPLATE_FILE if told else TEMPLATE_FILE)
    extra = {"prior_attempt": told} if told else {}
    system, user = template.render(
        task_brief=pack.public_brief(),
        priority=f"{priority}: {priority_text(priority)}",
        plan=render_plan(plan),
        latency_cycles=str(pack.latency_cycles),
        top=pack.top,
        **extra,
    )
    parsed = await call_parsed(
        llm,
        "rtl_gen",
        template=template,
        system=system,
        user=user,
        parse=lambda text: parse_rtl_answer(text, pack.top),
        tags={"task_id": pack.id, "pack_root": str(pack.root), "priority": priority, "attempt": "1"},
        format_hint=FORMAT_HINT,
        sampling={k: v for k, v in (("temperature", temperature), ("top_p", top_p)) if v is not None},
    )
    answer: RtlAnswer = parsed.value
    return RtlOut(text=answer.text, reason=answer.reason or DEFAULT_REASON, meta=parsed.meta, raw=parsed.raw)


def synthesize_rtl(pack: TaskPack, plan: PlanOut | None = None, priority: Priority = "area") -> RtlOut:
    """Deterministically synthesize complete synthesizable Verilog using the Verity Verilog Engine."""
    from ..core.llm_types import CallMeta
    from ..engine.synthesizer import PlanSynthesizer

    synth = PlanSynthesizer()
    text = synth.synthesize_verilog(pack, plan, priority=priority)
    meta = CallMeta(
        role="rtl_gen",
        model="verity-engine/1.0",
        request_id="engine-synth",
        prompt_version="engine.v1",
        input_tokens=0,
        output_tokens=len(text.splitlines()),
        latency_ms=1,
        usd=0.0,
    )
    return RtlOut(text=text, reason=f"Synthesized by Verity Verilog Engine for {priority}", meta=meta, raw=text)

