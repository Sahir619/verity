"""Optimizer role (operators SIMPLIFY, OPTIMIZE, EXPLORE): a parent design and its measured PPA in, a child design out.

Same pattern as REvolution's operators, at tiny scale: the child must keep the interface and the latency, and is
judged afterwards by the golden model and by PPA against our own v1. The prompt also carries where the parent's area
and delay are (common.measure_evidence): its largest cell types by area, its flip-flop count and its critical path.
"""

import hashlib
from collections.abc import Sequence

from ..contracts import PPA, Priority
from ..core.llm_types import RtlOut, TriedVersion
from ..core.pack import TaskPack
from ..core.protocols import LLMGateway
from ..core.results import MeasureResult
from .common import call_parsed, format_ppa, measure_evidence, priority_text, relative_note, tried_block
from .parsing import RtlAnswer, parse_rtl_answer
from .prompts import load_template

TEMPLATE_FILE = "optimize.v4.md"
FORMAT_HINT = "Give one REASON line, then exactly one ```verilog fenced block with the whole file."

STRATEGIES: dict[str, str] = {
    "SIMPLIFY": (
        "SIMPLIFY: make the design smaller by removing what is not needed. Look for redundant registers, signals wider "
        "than any value can use, duplicated logic, unused outputs of sub-expressions, and logic that can share a "
        "resource. Do not trade away behavior."
    ),
    "OPTIMIZE": (
        "OPTIMIZE: improve the chosen priority metric with a targeted change that keeps the same structure. For area, "
        "reduce logic depth of the biggest operator, share arithmetic, narrow datapaths that are provably safe. For "
        "speed, shorten the longest combinational path (fewer operators in series, fewer logic levels between "
        "registers) without changing the cycle latency."
    ),
    "EXPLORE": (
        "EXPLORE: try a clearly different micro-architecture that still gives identical cycle-by-cycle outputs, for "
        "example resource sharing across cycles, or a different counter or state encoding. Prefer an idea the parent "
        "does not already use."
    ),
}
DEFAULT_REASONS: dict[str, str] = {
    "SIMPLIFY": "Simplified the design",
    "OPTIMIZE": "Optimized the design",
    "EXPLORE": "Explored a different structure",
}


async def optimize(
    llm: LLMGateway,
    pack: TaskPack,
    parent_rtl: str,
    parent_ppa: PPA,
    baseline_ppa: PPA,
    priority: Priority,
    strategy: str,
    *,
    idea: str | None = None,
    parent_measure: MeasureResult | None = None,
    tried: Sequence[TriedVersion] = (),
) -> RtlOut:
    """`idea` (EXPLORE only): one known approach the parent does not use, as our own one-line text from the fixed
    technique list (verity.research.techniques). It replaces the generic EXPLORE idea; nothing else changes.
    `parent_measure`: the parent's synthesis and timing result, rendered as the evidence block.
    `tried`: the other finished versions of this run, rendered as the "already tried" list."""
    strategy = strategy.upper()
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {sorted(STRATEGIES)}")
    template = load_template(TEMPLATE_FILE)
    note = relative_note(parent_ppa, baseline_ppa)
    strategy_text = STRATEGIES[strategy]
    if strategy == "EXPLORE" and idea:
        strategy_text = (
            "EXPLORE: rebuild the design around this known approach, which the parent does not use, keeping identical "
            f"cycle-by-cycle outputs: {idea.strip()} Use the idea for what each register holds and when each operation "
            "happens; keep writing arithmetic with `*` and `+` as the coding rules say."
        )
    system, user = template.render(
        task_brief=pack.public_brief(),
        priority=f"{priority}: {priority_text(priority)}",
        strategy=strategy_text,
        parent_rtl=parent_rtl.strip("\n"),
        parent_ppa=format_ppa(parent_ppa) + (f" ({note})" if note else ""),
        baseline_ppa=format_ppa(baseline_ppa),
        parent_evidence=measure_evidence(parent_measure),
        tried=tried_block(tried, baseline_ppa),
        top=pack.top,
        latency_cycles=str(pack.latency_cycles),
    )
    parsed = await call_parsed(
        llm,
        "optimizer",
        template=template,
        system=system,
        user=user,
        parse=lambda text: parse_rtl_answer(text, pack.top),
        tags={
            "task_id": pack.id,
            "pack_root": str(pack.root),
            "strategy": strategy,
            "priority": priority,
            "attempt": "1",
            "parent_sha": hashlib.sha256(parent_rtl.encode("utf-8", errors="replace")).hexdigest(),
            "idea": "1" if strategy == "EXPLORE" and idea else "",
        },
        format_hint=FORMAT_HINT,
    )
    answer: RtlAnswer = parsed.value
    return RtlOut(
        text=answer.text,
        reason=answer.reason or DEFAULT_REASONS[strategy],
        meta=parsed.meta,
        raw=parsed.raw,
    )


def optimize_rtl(pack: TaskPack, priority: Priority = "area") -> RtlOut:
    """Deterministically optimize Verilog using the Verity Circuit Optimizer."""
    from ..core.llm_types import CallMeta
    from ..engine.optimizer import CircuitOptimizer

    opt = CircuitOptimizer()
    res = opt.optimize(pack, priority=priority)
    meta = CallMeta(
        role="optimizer",
        model="verity-engine/1.0",
        request_id="engine-opt",
        prompt_version="engine.v1",
        input_tokens=0,
        output_tokens=len(res.verilog.splitlines()),
        latency_ms=1,
        usd=0.0,
    )
    return RtlOut(text=res.verilog, reason=res.strategy, meta=meta, raw=res.verilog)

