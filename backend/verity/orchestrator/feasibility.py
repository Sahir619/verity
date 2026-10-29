"""Deterministic feasibility gate (ARCHITECTURE 10.2). Code decides; the Planner only explains.

`check_plan` returns human-readable reasons. An empty list means the plan may proceed. This is a scope filter, not
a proof that a design can be built. It runs before any RTL token is spent.

What is NOT checked here, on purpose: port widths and signedness. The plan carries them as free text
(`interfaces`, `dataWidths`), so a text match would reject good plans. The widths are enforced where they can be
checked exactly: `/verity/bin/verify` compares the RTL's ports with task.toml (Yosys JSON) before simulation, and
a repair cannot widen a port to "fix" a failure.
"""

import re

from ..config import Settings
from ..core.pack import GOLDEN_FILE, TESTBENCH_FILE
from ..core.types import PlanOut, TaskPack
from ..fabrication import check_chip_fabricability, extract_chip_spec_from_plan

ALLOWED_ELEMENT_TYPES = frozenset({"int4", "uint4", "int8", "uint8", "none"})

# Things the toolchain and coding rules cannot do. Matched against block names and block reasons only (not the
# risks list, where a plan may legitimately say "no PLL is needed").
_OUT_OF_SCOPE = re.compile(
    r"\b(pll|sram|dram|serdes|analog|vendor ip|hard macro|multi-clock|dual-clock|multiple clocks|"
    r"clock domain crossing|cdc)\b",
    re.IGNORECASE,
)


def check_plan(plan: PlanOut, pack: TaskPack, settings: Settings) -> list[str]:
    reasons: list[str] = []

    # Verifiable: no golden model and testbench, no run.
    for required in (GOLDEN_FILE, TESTBENCH_FILE):
        if not (pack.root / required).is_file():
            reasons.append(f"the task pack has no {required}, so the design cannot be verified")

    if pack.element_type not in ALLOWED_ELEMENT_TYPES:
        reasons.append(f"element type {pack.element_type} is not supported")

    # Size.
    max_blocks = min(settings.plan.max_blocks, pack.max_plan_blocks)
    if not plan.blocks:
        reasons.append("the plan has no blocks")
    elif len(plan.blocks) > max_blocks:
        reasons.append(f"the plan has {len(plan.blocks)} blocks, the limit is {max_blocks}")
    if plan.estimated_lines > pack.max_lines:
        reasons.append(f"the plan estimates {plan.estimated_lines} lines, the limit is {pack.max_lines}")
    if plan.estimated_lines < 1:
        reasons.append("the plan gives no line estimate")

    # Latency is fixed by the pack (needed for EQY and enforced by the testbench).
    if plan.latency_cycles != pack.latency_cycles:
        reasons.append(f"the plan uses {plan.latency_cycles} cycles of latency, the task requires {pack.latency_cycles}")

    # Block sanity.
    seen: set[str] = set()
    for block in plan.blocks:
        if block.id in seen:
            reasons.append(f"duplicate block id {block.id}")
        seen.add(block.id)
        for variant in block.variants:
            if variant.value not in variant.options:
                reasons.append(f"block {block.name}: variant {variant.id} is set to '{variant.value}', not one of its options")
        if _OUT_OF_SCOPE.search(f"{block.name} {block.reason}"):
            reasons.append(f"block {block.name} asks for something outside the supported toolchain")

    # Deterministic machine check and materials science limits (e.g. SOTA ASML High-NA EUV, Silicon tunneling).
    chip_spec = extract_chip_spec_from_plan(plan, pack)
    fab_result = check_chip_fabricability(chip_spec)
    if not fab_result.can_build:
        reasons.extend(fab_result.reasons)

    return reasons

