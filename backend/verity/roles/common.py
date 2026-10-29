"""Shared helpers for the four roles: the call-parse-retry loop, call metadata, small prompt formatters."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from ..contracts import PPA, Priority
from ..core.captions import clean
from ..core.errors import RoleOutputError
from ..core.llm_types import CallMeta, LLMResponse, PlanOut, ResponseFormat, TriedVersion
from ..core.protocols import LLMGateway
from ..core.results import CriticalPath, MeasureResult
from .parsing import ParseError
from .prompts import PromptTemplate

T = TypeVar("T")

PRIORITY_TEXT: dict[str, str] = {
    "area": "smallest area (fewest and smallest cells)",
    "speed": "shortest clock period (highest clock speed)",
    "power": "lowest estimated power (keep the logic small and avoid needless toggling)",
}

RETRY_NOTE = (
    "Your previous answer could not be used: {error}.\n"
    "Answer again from the start, following the output format exactly. {format_hint}"
)


@dataclass(frozen=True, slots=True)
class Parsed(Generic[T]):
    value: T
    meta: CallMeta
    raw: str


def meta_from(template: PromptTemplate, response: LLMResponse, usd: float, role: str) -> CallMeta:
    return CallMeta(
        role=role,  # type: ignore[arg-type]
        model=response.model,
        request_id=response.request_id,
        prompt_version=template.version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
        usd=usd,
        finish_reason=response.finish_reason,
        reasoning_tokens=response.reasoning_tokens,
    )


def merge_meta(first: CallMeta, second: CallMeta) -> CallMeta:
    """Two calls for one role result (a parse retry): sum tokens, latency and cost; keep every request id."""
    return CallMeta(
        role=second.role,
        model=second.model,
        request_id=f"{first.request_id},{second.request_id}",
        prompt_version=second.prompt_version,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        latency_ms=first.latency_ms + second.latency_ms,
        usd=first.usd + second.usd,
        finish_reason=",".join(r for r in (first.finish_reason, second.finish_reason) if r),
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
    )


def json_retries(llm: LLMGateway) -> int:
    """plan.json_retries from the gateway's settings when it exposes them, else 1."""
    settings = getattr(llm, "settings", None)
    plan = getattr(settings, "plan", None)
    return int(getattr(plan, "json_retries", 1))


async def call_parsed(
    llm: LLMGateway,
    role: str,
    *,
    template: PromptTemplate,
    system: str,
    user: str,
    parse: Callable[[str], T],
    tags: Mapping[str, str],
    format_hint: str,
    response_format: ResponseFormat = "text",
    retries: int = 1,
    sampling: Mapping[str, float] | None = None,
) -> Parsed[T]:
    """Call the model, parse the text; on ParseError ask again (up to `retries` times) with the error appended.

    BudgetExhausted and LLMError from the gateway propagate untouched. After the last failed parse raises
    RoleOutputError carrying the raw text of every attempt.
    """
    meta: CallMeta | None = None
    raws: list[str] = []
    current_user = user
    last_error = ""
    for attempt in range(retries + 1):
        request_tags = dict(tags)
        if attempt:
            request_tags["parse_retry"] = str(attempt)
        response, usd = await llm.call(
            role,  # type: ignore[arg-type]
            system=system,
            user=current_user,
            response_format=response_format,
            prompt_version=template.version,
            tags=request_tags,
            **(sampling or {}),
        )
        call_meta = meta_from(template, response, usd, role)
        meta = call_meta if meta is None else merge_meta(meta, call_meta)
        raws.append(response.text)
        try:
            value = parse(response.text)
        except ParseError as exc:
            last_error = str(exc)
            if response.finish_reason == "length":
                last_error += " (the model hit the output token limit)"
            current_user = user + "\n\n" + RETRY_NOTE.format(error=last_error, format_hint=format_hint)
            continue
        return Parsed(value=value, meta=meta, raw="\n\n----- retry -----\n\n".join(raws))
    raise RoleOutputError(role, last_error or "unusable answer", raw="\n\n----- retry -----\n\n".join(raws))


# ---------- prompt formatters ----------
def priority_text(priority: Priority) -> str:
    return PRIORITY_TEXT.get(priority, PRIORITY_TEXT["area"])


def render_plan(plan: PlanOut) -> str:
    """Compact, human-readable plan for the RTL prompt (blocks with their pins and chosen variant values)."""
    lines = [f"Summary: {plan.summary}"]
    for block in plan.blocks:
        extras: list[str] = []
        if block.pins:
            extras.append("pinned: " + "; ".join(block.pins))
        for variant in block.variants:
            extras.append(f"{variant.label} = {variant.value} (options: {', '.join(variant.options)})")
        suffix = f" [{' | '.join(extras)}]" if extras else ""
        lines.append(f"- {block.name}: {block.reason}{suffix}")
    if plan.data_widths:
        lines.append(f"Data widths: {plan.data_widths}")
    lines.append(f"Latency: {plan.latency_cycles} cycles")
    if plan.risks:
        lines.append("Risks: " + "; ".join(plan.risks))
    return "\n".join(lines)


def format_ppa(ppa: PPA) -> str:
    area = f"{ppa.area_um2:.1f} um2" if ppa.area_um2 is not None else "not measured"
    cells = f"{ppa.cell_count} cells" if ppa.cell_count is not None else "cells unknown"
    clock = f"{ppa.clock_period_ns:.2f} ns" if ppa.clock_period_ns is not None else "not measured"
    power = f"{ppa.power_mw:.3f} mW (estimate)" if ppa.power_mw is not None else "not measured"
    return f"area {area}, {cells}, clock period {clock}, power {power}"


def relative_note(parent: PPA, baseline: PPA) -> str:
    """How the parent compares with v1, in plain words. Empty when there is nothing to compare."""
    parts: list[str] = []
    for label, now, base in (
        ("area", parent.area_um2, baseline.area_um2),
        ("clock period", parent.clock_period_ns, baseline.clock_period_ns),
    ):
        if now is None or base is None or base == 0:
            continue
        change = 100.0 * (now - base) / base
        parts.append(f"{label} {change:+.1f}% vs v1")
    return ", ".join(parts)


NOTHING_TRIED = "- Nothing yet: no other version of this run has finished."


def tried_block(tried: Sequence[TriedVersion], baseline: PPA) -> str:
    """One bullet per earlier version of this run: its strategy, its own reason and what it measured against v1 (or
    that it was rejected). The numbers come from the measurements; the reason is that version's own text, cleaned."""
    if not tried:
        return NOTHING_TRIED
    lines = []
    for item in tried:
        reason = clean(item.reason, 140).rstrip(". ")
        note = relative_note(item.ppa, baseline) if item.ppa is not None else ""
        result = f"measured {note}" if note else ("measured, no comparison" if item.ppa is not None else "rejected")
        lines.append(f"- {item.label} ({item.strategy}): {reason}. Result: {result}.")
    return "\n".join(lines)


# ---------- optimizer evidence: where the area and the delay are ----------
EVIDENCE_TOP_CELLS = 6
_SEQUENTIAL_CELL = re.compile(r"^(S?DFF|DL[HL]|TLAT)", re.IGNORECASE)
_STAT_ROW = re.compile(r"^\s+(\d+)\s+([0-9][0-9.]*(?:[eE][-+]?\d+)?)\s+([A-Za-z_][\w$.]*)\s*$", re.MULTILINE)
_STA_POINT = re.compile(r"^(Startpoint|Endpoint):\s+(\S+)\s+\((.*?)\)\s*$", re.MULTILINE)
_STA_ROW = re.compile(r"^\s*-?[\d.]+\s+-?[\d.]+\s+[\^v]\s+\S+?/\w+\s+\((\w+)\)\s*$", re.MULTILINE)
_STA_ARRIVAL = re.compile(r"^\s*(-?[\d.]+)\s+data arrival time", re.MULTILINE)
_GENERATED_NAME = re.compile(r"^_\d+_$")


def _cell_areas(measure: MeasureResult) -> dict[str, float]:
    """Area per cell type: from the sandbox (newer images), else from the `stat -liberty` table in the synth log."""
    synth = measure.synth
    if synth is None:
        return {}
    if synth.cell_area:
        return dict(synth.cell_area)
    return {name: float(area) for _n, area, name in _STAT_ROW.findall(synth.log) if name != "cells"}


def _log_path(log: str) -> CriticalPath | None:
    """The worst path from an OpenSTA log when the sandbox did not send one (older images). Flip-flop instance names
    cannot be mapped to register names here, so generated names are left out."""
    points = {kind: (name, desc) for kind, name, desc in _STA_POINT.findall(log)}
    if "Startpoint" not in points or "Endpoint" not in points:
        return None
    body = log[log.find("Startpoint:") :]
    arrival = _STA_ARRIVAL.search(body)
    if arrival:
        body = body[: arrival.start()]

    def kind(desc: str) -> str:
        return "register" if "flip-flop" in desc else "input" if "input port" in desc else "output" if "output port" in desc else "other"

    def name(raw: str) -> str:
        return "" if _GENERATED_NAME.match(raw) else raw

    return CriticalPath(
        startpoint=name(points["Startpoint"][0]),
        endpoint=name(points["Endpoint"][0]),
        start_kind=kind(points["Startpoint"][1]),
        end_kind=kind(points["Endpoint"][1]),
        depth=sum(1 for cell in _STA_ROW.findall(body) if not _SEQUENTIAL_CELL.match(cell)),
        delay_ns=float(arrival.group(1)) if arrival else None,
    )


def _point(kind: str, name: str) -> str:
    noun = {"register": "register", "input": "input", "output": "output"}.get(kind, "point")
    return f"{noun} {name}".strip() if name else f"a {noun}"


def measure_evidence(measure: MeasureResult | None) -> str:
    """Markdown bullets for the optimize prompt: the largest cell types by area, the flip-flops, the critical path.
    Numbers come straight from the parent's Yosys and OpenSTA results."""
    if measure is None or measure.synth is None or not measure.synth.ok:
        return "- Not measured."
    synth = measure.synth
    lines: list[str] = []
    areas = _cell_areas(measure)
    total = synth.area_um2 or sum(areas.values())
    if areas:
        top = sorted(areas.items(), key=lambda kv: (-kv[1], kv[0]))[:EVIDENCE_TOP_CELLS]
        parts = []
        for name, area in top:
            share = f", {100.0 * area / total:.0f}%" if total else ""
            parts.append(f"{name} ({synth.cells.get(name, 0)} cells, {area:.1f} um2{share})")
        lines.append("- Largest cell types by area: " + "; ".join(parts))
    elif synth.cells:
        top_counts = sorted(synth.cells.items(), key=lambda kv: (-kv[1], kv[0]))[:EVIDENCE_TOP_CELLS]
        lines.append("- Most used cell types: " + "; ".join(f"{name} ({count} cells)" for name, count in top_counts))
    flops = {name: count for name, count in synth.cells.items() if _SEQUENTIAL_CELL.match(name)}
    flop_area = sum(areas.get(name, 0.0) for name in flops)
    flop_line = f"- Flip-flops: {sum(flops.values())}"
    if flops and flop_area:
        flop_line += f" ({flop_area:.1f} um2" + (f", {100.0 * flop_area / total:.0f}% of the area)" if total else ")")
    lines.append(flop_line)
    sta = measure.sta
    path = None
    if sta is not None and sta.ok:
        path = sta.path or _log_path(sta.log)
    if path is not None:
        delay = f", {path.delay_ns:.3f} ns" if path.delay_ns is not None else ""
        lines.append(
            f"- Critical path: from {_point(path.start_kind, path.startpoint)} to {_point(path.end_kind, path.endpoint)}, "
            f"{path.depth} logic stage{'s' if path.depth != 1 else ''}{delay}"
        )
    else:
        lines.append("- Critical path: not measured.")
    return "\n".join(lines)
