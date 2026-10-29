"""Planner role: spec text + pack facts in, a PlanOut (blocks the user can steer) out. One JSON object."""

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from ..contracts import PinInput, Priority, ToggleInput, to_wire
from ..core.llm_types import PlanOut, PlanResult
from ..core.pack import TaskPack
from ..core.protocols import LLMGateway
from .common import call_parsed, json_retries, priority_text
from .parsing import ParseError, extract_json_object
from .prompts import load_template

TEMPLATE_FILE = "planner.v2.md"
FORMAT_HINT = "Reply with exactly one JSON object and nothing else."


async def plan(
    llm: LLMGateway,
    pack: TaskPack,
    priority: Priority,
    *,
    previous: PlanOut | None = None,
    steering: Sequence[PinInput | ToggleInput] = (),
    references: str = "",
) -> PlanResult:
    """Revision 1 when `previous` is None, otherwise a replan that honors every pin and toggle in `steering`.

    `references` is the "Known approaches" section built by code (verity.research.service.RunReferences): technique
    labels from the fixed list and code-written past outcome lines only, never web, summary or user text.
    Retries once on invalid JSON (plan.json_retries), then raises RoleOutputError.
    """
    template = load_template(TEMPLATE_FILE)
    revision = _revision_section(previous, steering)
    system, user = template.render(
        task_brief=pack.public_brief(),
        priority=f"{priority}: {priority_text(priority)}",
        latency_cycles=str(pack.latency_cycles),
        max_lines=str(pack.max_lines),
        max_blocks=str(pack.max_plan_blocks),
        revision_section=f"{references.strip()}\n\n{revision}" if references.strip() else revision,
    )
    tags = {
        "task_id": pack.id,
        "pack_root": str(pack.root),
        "priority": priority,
        "attempt": "2" if previous is not None else "1",
        "steering": json.dumps([_steer_wire(s) for s in steering], sort_keys=True),
    }
    parsed = await call_parsed(
        llm,
        "planner",
        template=template,
        system=system,
        user=user,
        parse=_parse_plan,
        tags=tags,
        format_hint=FORMAT_HINT,
        response_format="json",
        retries=json_retries(llm),
    )
    return PlanResult(plan=parsed.value, meta=parsed.meta, raw=parsed.raw)


# ---------- prompt pieces ----------
def _steer_wire(item: PinInput | ToggleInput) -> dict[str, str]:
    if isinstance(item, PinInput):
        return {"kind": "pin", "blockId": item.block_id, "pin": item.pin}
    return {"kind": "toggle", "blockId": item.block_id, "variantId": item.variant_id, "value": item.value}


def _revision_section(previous: PlanOut | None, steering: Sequence[PinInput | ToggleInput]) -> str:
    if previous is None:
        return "Write the first version of the plan."
    names = {block.id: block.name for block in previous.blocks}
    lines = [
        "The user steered the previous plan. Write the full updated plan, keeping block ids stable.",
        "Apply every instruction below: a pin is added to that block's `pins` list (and the block design should follow it); "
        "a toggle sets that variant's `value`. You may adjust other blocks only where an instruction requires it.",
        "",
        "Instructions from the user:",
    ]
    for item in steering:
        label = names.get(item.block_id, item.block_id)
        if isinstance(item, PinInput):
            lines.append(f'- Pin "{item.pin}" on block {item.block_id} ({label}).')
        else:
            lines.append(f'- Set "{item.variant_id}" to "{item.value}" on block {item.block_id} ({label}).')
    lines += ["", "Previous plan:", "```json", json.dumps(to_wire(previous), indent=1), "```"]
    return "\n".join(lines)


# ---------- parsing ----------
def _parse_plan(text: str) -> PlanOut:
    data = _normalize(extract_json_object(text))
    try:
        plan_out = PlanOut.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5])
        raise ParseError(f"the JSON does not match the plan schema ({problems})") from exc
    if not plan_out.blocks:
        raise ParseError("the plan has no blocks")
    return plan_out


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(_as_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return "" if value is None else str(value)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Make small, harmless repairs to a model's JSON (wrapped object, list instead of string, bad variant value)."""
    if "blocks" not in data and isinstance(data.get("plan"), dict):
        data = dict(data["plan"])
    data = dict(data)
    for key in ("interfaces", "dataWidths", "data_widths", "verification", "summary"):
        if key in data:
            data[key] = _as_text(data[key])
    risks = data.get("risks")
    if isinstance(risks, str):
        data["risks"] = [risks]
    elif isinstance(risks, list):
        data["risks"] = [_as_text(r) for r in risks]

    blocks = data.get("blocks")
    if isinstance(blocks, list):
        fixed_blocks: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict):
                fixed_blocks.append(block)
                continue
            block = dict(block)
            block.setdefault("reason", block.get("name", ""))
            for key in ("pinOptions", "pin_options", "pins"):
                if isinstance(block.get(key), str):
                    block[key] = [block[key]]
            variants = block.get("variants")
            if isinstance(variants, list):
                fixed_variants: list[Any] = []
                for variant in variants:
                    if isinstance(variant, dict):
                        variant = dict(variant)
                        options = variant.get("options")
                        if isinstance(options, list) and options:
                            variant["options"] = [_as_text(o) for o in options]
                            if _as_text(variant.get("value")) not in variant["options"]:
                                variant["value"] = variant["options"][0]
                            else:
                                variant["value"] = _as_text(variant["value"])
                        variant.setdefault("label", variant.get("id", ""))
                    fixed_variants.append(variant)
                block["variants"] = fixed_variants
            fixed_blocks.append(block)
        data["blocks"] = fixed_blocks
    return data
