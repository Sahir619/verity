"""Pure helpers for plan blocks: id normalization, diffs, and applying steering in code (no LLM)."""

import re
from collections.abc import Sequence

from ..contracts import PlanBlock, PlanDiff, Variant
from ..core.types import PlanOut

_BAD_CHARS = re.compile(r"[^a-z0-9-]+")
_MAX_ID_LEN = 40


def slug_block_id(raw: str, taken: set[str]) -> str:
    """`blk-<slug>`: lowercase, [a-z0-9-] only, unique within `taken` (which is updated)."""
    slug = _BAD_CHARS.sub("-", raw.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if slug.startswith("blk-"):
        slug = slug[len("blk-") :]
    slug = slug.strip("-") or "block"
    base = ("blk-" + slug)[:_MAX_ID_LEN].rstrip("-")
    candidate = base
    n = 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = base[: _MAX_ID_LEN - len(suffix)] + suffix
        n += 1
    taken.add(candidate)
    return candidate


def normalize_plan(plan: PlanOut) -> PlanOut:
    """Same plan with canvas-safe, unique block ids. Deterministic: the same raw ids always map to the same ids."""
    taken: set[str] = set()
    blocks = [block.model_copy(update={"id": slug_block_id(block.id, taken)}) for block in plan.blocks]
    return plan.model_copy(update={"blocks": blocks})


def _signature(block: PlanBlock) -> tuple:
    return (
        block.name,
        block.reason,
        tuple(block.pin_options),
        tuple(block.pins),
        tuple((v.id, v.label, tuple(v.options), v.value) for v in block.variants),
    )


def diff_blocks(old: Sequence[PlanBlock], new: Sequence[PlanBlock]) -> PlanDiff:
    old_by_id = {b.id: b for b in old}
    new_by_id = {b.id: b for b in new}
    return PlanDiff(
        added=[b.id for b in new if b.id not in old_by_id],
        changed=[b.id for b in new if b.id in old_by_id and _signature(b) != _signature(old_by_id[b.id])],
        removed=[b.id for b in old if b.id not in new_by_id],
    )


def find_block(plan: PlanOut, block_id: str) -> PlanBlock | None:
    return next((b for b in plan.blocks if b.id == block_id), None)


def find_variant(block: PlanBlock, variant_id: str) -> Variant | None:
    return next((v for v in block.variants if v.id == variant_id), None)


def apply_pin(plan: PlanOut, block_id: str, pin: str) -> tuple[PlanOut, str | None]:
    """Pin an option on a block in code. Returns (plan, change note); note is None when nothing changed."""
    block = find_block(plan, block_id)
    if block is None or pin in block.pins or (block.pin_options and pin not in block.pin_options):
        return plan, None
    updated = block.model_copy(update={"pins": [*block.pins, pin]})
    return _replace(plan, updated), f"pinned {pin}"


def apply_toggle(plan: PlanOut, block_id: str, variant_id: str, value: str) -> tuple[PlanOut, str | None]:
    block = find_block(plan, block_id)
    if block is None:
        return plan, None
    variant = find_variant(block, variant_id)
    if variant is None or value not in variant.options or variant.value == value:
        return plan, None
    variants = [v.model_copy(update={"value": value}) if v.id == variant_id else v for v in block.variants]
    updated = block.model_copy(update={"variants": variants})
    return _replace(plan, updated), f"{value} {variant.label.lower()}"


def _replace(plan: PlanOut, block: PlanBlock) -> PlanOut:
    return plan.model_copy(update={"blocks": [block if b.id == block.id else b for b in plan.blocks]})
