"""Sandbox JSON (already a dict) into the typed core results (CONTRACTS 3.5 to 3.7).

Rules:
- A payload that does not fit the result model raises pydantic.ValidationError. The runner treats that as an
  infrastructure error (retry once), distinct from a design that fails its tests.
- A run that printed a "pass" but did not execute the full vector count from task.toml is downgraded to
  `sim_fail`. A truncated run never counts as pass.
- Non-finite or negative numbers in a measurement are dropped (the metric becomes missing), never trusted.
"""

import math
from typing import Any

from ..contracts import CircuitGraph
from ..core.results import (
    EquivalenceResult,
    GoldenCheckResult,
    MeasureResult,
    PowerResult,
    SeedsResult,
    StrengthResult,
    SynthResult,
    TimingResult,
    VerifyResult,
    WaveResult,
)


def parse_verify(payload: dict[str, Any], *, expected_total: int) -> VerifyResult:
    result = VerifyResult.model_validate(payload)
    if result.status != "pass":
        return result
    if result.sim is None:
        return result.model_copy(update={"status": "sim_error", "message": "verify reported pass without a simulation result"})
    if not result.passed_all(expected_total):
        sim = result.sim
        return result.model_copy(
            update={
                "status": "sim_fail",
                "message": f"simulation ran {sim.total} cases and {sim.passed} passed, expected {expected_total} of {expected_total}",
            }
        )
    return result


def _finite_nonneg(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return value


def _clean_synth(synth: SynthResult) -> SynthResult:
    area = _finite_nonneg(synth.area_um2)
    ok = synth.ok and area is not None
    cells = {name: count for name, count in synth.cells.items() if count >= 0}
    cell_count = synth.cell_count
    if cell_count is None and cells:
        cell_count = sum(cells.values())
    return synth.model_copy(update={"ok": ok, "area_um2": area, "cell_count": cell_count, "cells": cells})


def _clean_sta(sta: TimingResult) -> TimingResult:
    period = sta.clock_period_ns
    if period is not None and (not math.isfinite(period) or period <= 0):
        period = None
    wns = sta.wns_ns if sta.wns_ns is not None and math.isfinite(sta.wns_ns) else None
    ok = sta.ok and period is not None
    return sta.model_copy(update={"ok": ok, "clock_period_ns": period, "wns_ns": wns})


def _clean_power(power: PowerResult) -> PowerResult:
    total = power.power_mw
    if total is not None and (not math.isfinite(total) or total <= 0):
        total = None
    return power.model_copy(update={"ok": power.ok and total is not None, "power_mw": total})


def _circuit_is_consistent(circuit: CircuitGraph) -> bool:
    """Every edge endpoint must be a node, and node ids must be unique. Otherwise the web viewer would break."""
    ids = [node.id for node in circuit.nodes]
    if len(ids) != len(set(ids)):
        return False
    known = set(ids)
    return all(edge.source in known and edge.target in known for edge in circuit.edges)


def parse_measure(payload: dict[str, Any]) -> MeasureResult:
    result = MeasureResult.model_validate(payload)
    updates: dict[str, Any] = {}
    if result.synth is not None:
        updates["synth"] = _clean_synth(result.synth)
    if result.sta is not None:
        updates["sta"] = _clean_sta(result.sta)
    if result.power is not None:
        updates["power"] = _clean_power(result.power)
    if result.circuit is not None and not _circuit_is_consistent(result.circuit):
        updates["circuit"] = None
    result = result.model_copy(update=updates) if updates else result

    if result.status == "ok" and not result.measured:
        # "ok" without a usable area is a synthesis failure, whatever the script said.
        result = result.model_copy(
            update={"status": "synth_fail", "message": result.message or "synthesis reported ok without an area"}
        )
    elif result.status == "ok" and (result.sta is None or not result.sta.ok):
        # Area is valid but timing is not: keep the area and report the timing failure honestly.
        result = result.model_copy(update={"status": "sta_fail"})
    return result


def parse_eqy(payload: dict[str, Any]) -> EquivalenceResult:
    return EquivalenceResult.model_validate(payload)


def parse_seeds(payload: dict[str, Any], *, expected_total: int) -> SeedsResult:
    """A seed counts as passed only when it ran the full case count from task.toml (same rule as verify)."""
    result = SeedsResult.model_validate(payload)
    seeds = [
        run.model_copy(update={"ok": run.ok and run.total == expected_total and run.passed == expected_total})
        for run in result.seeds
    ]
    return result.model_copy(update={"seeds": seeds})


def parse_strength(payload: dict[str, Any]) -> StrengthResult:
    return StrengthResult.model_validate(payload)


def parse_golden_check(payload: dict[str, Any], *, expected_total: int) -> GoldenCheckResult:
    """A run that did not execute every case (a bad cases.json, not the draft's fault) is downgraded to
    internal_error, the same "truncated run never counts as pass" rule verify uses."""
    result = GoldenCheckResult.model_validate(payload)
    if result.status == "pass" and result.total != expected_total:
        return result.model_copy(
            update={"status": "internal_error", "message": f"golden_check ran {result.total} cases, expected {expected_total}"}
        )
    return result


def parse_wave(payload: dict[str, Any]) -> WaveResult:
    """A wave whose rows disagree in length is dropped (status sim_error), never shown misaligned."""
    result = WaveResult.model_validate(payload)
    wave = result.wave
    if wave is None or not wave.signals:
        return result
    lengths = {len(s.got) for s in wave.signals} | {len(s.expected) for s in wave.signals if s.expected is not None}
    if len(lengths) != 1 or 0 in lengths:
        return result.model_copy(update={"status": "sim_error", "wave": None, "message": "the waveform rows have different lengths"})
    return result
