"""Shared cocotb helpers for Verity task packs (CONTRACTS section 4).

Installed in the tool image at /verity/lib (on PYTHONPATH). Task pack testbenches import:

    from verity_tb import Recorder, read_int, fmt_hex, fmt_lanes, start_clock, reset_dut

Conventions every pack follows (so the timing is race free and the same for all designs):
  * the clock starts low; the testbench changes inputs and samples outputs only on the FALLING edge,
    so every rising edge sees stable inputs and every sample sees settled outputs;
  * a purely combinational design is driven, then sampled 1 ns later (no clock);
  * a case never stops at the first mismatch inside the testbench: it is recorded and the next case runs.

UNVERIFIED: written against cocotb 1.9.2. Version differences (Clock units/unit, Timer units/unit,
BinaryValue vs LogicArray) are absorbed in the helpers below; check on the first real simulation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

MAX_FAILURES = 5
MAX_INPUTS_CHARS = 200
SCHEMA = "verity.sim/1"


# ---------- value helpers ----------
def read_int(handle: Any) -> int | None:
    """Unsigned integer value of a signal, or None if any bit is X or Z (or the read fails)."""
    try:
        value = handle.value
    except Exception:  # noqa: BLE001  (a missing or unreadable handle is a failed read, not a crash)
        return None
    resolvable = getattr(value, "is_resolvable", None)
    if resolvable is not None and not resolvable:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def fmt_hex(value: int | None, bits: int) -> str:
    """'0x3F8040' with one hex digit per 4 bits of the output width; 'X' when the value is unknown."""
    if value is None:
        return "X"
    digits = max(1, (bits + 3) // 4)
    return "0x" + format(value & ((1 << bits) - 1), f"0{digits}X")


def fmt_lanes(values: list[int]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def pack_lanes(values: list[int], width: int) -> int:
    """Lane i goes to bits [i*width +: width] of one flat vector (CONTRACTS S2)."""
    mask = (1 << width) - 1
    out = 0
    for i, v in enumerate(values):
        out |= (v & mask) << (i * width)
    return out


def unpack_lanes(flat: int, width: int, count: int, *, signed: bool = False) -> list[int]:
    mask = (1 << width) - 1
    lanes = []
    for i in range(count):
        v = (flat >> (i * width)) & mask
        if signed and v >> (width - 1):
            v -= 1 << width
        lanes.append(v)
    return lanes


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------- clock and reset ----------
def start_clock(dut: Any, name: str = "clk", period_ns: int = 10) -> Any:
    """Start a clock that begins LOW (so X to 0 is not a rising edge). Returns the clock signal handle."""
    signal = getattr(dut, name)
    try:
        clock = Clock(signal, period_ns, unit="ns")  # cocotb 2.x
    except TypeError:
        clock = Clock(signal, period_ns, units="ns")  # cocotb 1.x
    cocotb.start_soon(clock.start(start_high=False))
    return signal


async def settle(ns: int = 1) -> None:
    """Let combinational logic settle after driving inputs (no clock edge)."""
    await Timer(ns, "ns")


async def reset_dut(clk: Any, rst: Any, *, active_low: bool = True, cycles: int = 3) -> None:
    """Hold reset for `cycles` rising edges, release on a falling edge. Works for sync and async resets."""
    await FallingEdge(clk)
    rst.value = 0 if active_low else 1
    for _ in range(cycles):
        await RisingEdge(clk)
    await FallingEdge(clk)
    rst.value = 1 if active_low else 0


# ---------- result recording ----------
class Recorder:
    """Collects one entry per case and writes $VERITY_SIM_RESULT (schema verity.sim/1)."""

    def __init__(self, top: str) -> None:
        self.top = top
        self.seed = env_int("VERITY_SEED", 1)
        self.path = Path(os.environ.get("VERITY_SIM_RESULT", "sim_result.json"))
        self._cases: list[dict[str, Any]] = []
        self._failures: list[dict[str, Any]] = []
        self._failure_count = 0

    def record(
        self,
        name: str,
        passed: bool,
        *,
        inputs: str = "",
        expected: str = "",
        got: str = "",
        cycle: int | None = None,
    ) -> None:
        self._cases.append({"name": name, "pass": bool(passed)})
        if passed:
            return
        self._failure_count += 1
        if len(self._failures) < MAX_FAILURES:
            self._failures.append(
                {
                    "name": name,
                    "inputs": inputs[:MAX_INPUTS_CHARS],
                    "expected": expected,
                    "got": got,
                    "pass": False,
                    "cycle": cycle,
                }
            )

    @property
    def total(self) -> int:
        return len(self._cases)

    @property
    def passed(self) -> int:
        return self.total - self._failure_count

    @property
    def all_passed(self) -> bool:
        return self._failure_count == 0 and self.total > 0

    def finish(self) -> bool:
        """Write the result file and return True when every case passed. Call before the final assert."""
        payload = {
            "schema": SCHEMA,
            "top": self.top,
            "total": self.total,
            "passed": self.passed,
            "failure_count": self._failure_count,
            "failures": self._failures,
            "cases": self._cases,
            "seed": self.seed,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
        return self.all_passed


# ---------- running a list of cases ----------
@dataclass(frozen=True)
class Outcome:
    """Result of one case. `expected` and `got` are short human strings (hex values, or a sentence)."""

    ok: bool
    expected: str = ""
    got: str = ""
    cycle: int | None = None
    inputs: str = ""  # optional: the specific failing stimulus; falls back to the case description


PASSED = Outcome(True)


async def run_cases(
    rec: Recorder,
    cases: Sequence[Any],
    run_one: Callable[[Any], Awaitable[Outcome]],
) -> None:
    """Run every case (never stopping at the first failure), record each, write the result file, then assert.

    Each case object needs `.name` and `.inputs`. The number of cases must equal task.toml `test_count`
    (passed as $VERITY_TEST_COUNT); a mismatch aborts before any case runs, so a pack bug cannot pass silently.
    """
    expected_count = env_int("VERITY_TEST_COUNT", len(cases))
    assert len(cases) == expected_count, f"pack defines {len(cases)} cases, task.toml test_count is {expected_count}"
    if os.environ.get("VERITY_WAVE_TRACE"):
        await _run_wave(cases, run_one)
        return
    for case in cases:
        try:
            outcome = await run_one(case)
        except Exception as exc:  # noqa: BLE001  (a testbench exception is a failed case, not a crashed run)
            outcome = Outcome(False, "case runs to completion", f"testbench exception: {type(exc).__name__}: {exc}"[:200])
        rec.record(case.name, outcome.ok, inputs=outcome.inputs or case.inputs, expected=outcome.expected, got=outcome.got, cycle=outcome.cycle)
    all_ok = rec.finish()
    assert all_ok, f"{rec.total - rec.passed} of {rec.total} cases failed"


# ---------- waveform mode (/verity/bin/wave only) ----------
def _sim_time_ns() -> float:
    from cocotb.utils import get_sim_time

    try:
        return float(get_sim_time(units="ns"))  # cocotb 1.x
    except TypeError:
        return float(get_sim_time(unit="ns"))  # cocotb 2.x


async def _run_wave(cases: Sequence[Any], run_one: Callable[[Any], Awaitable[Outcome]]) -> None:
    """Set by /verity/bin/wave through VERITY_WAVE_TRACE. Runs the cases in order (so every earlier case consumes the
    random generator exactly as in a normal run) and stops after the target case (VERITY_WAVE_CASE, default the first
    case). For a clocked design it then lets VERITY_WAVE_AFTER more falling edges pass with the last inputs held. It
    writes when each case started and ended, and its verdict, to the trace file. No result file, no assertion."""
    target = os.environ.get("VERITY_WAVE_CASE", "") or (cases[0].name if cases else "")
    clock_name = os.environ.get("VERITY_WAVE_CLOCK", "")
    after = env_int("VERITY_WAVE_AFTER", 4)
    trace: dict[str, Any] = {"target": target, "found": False, "cases": []}
    for case in cases:
        start = _sim_time_ns()
        try:
            outcome = await run_one(case)
        except Exception as exc:  # noqa: BLE001  (same rule as run_cases: an exception is a failed case)
            outcome = Outcome(False, "case runs to completion", f"testbench exception: {type(exc).__name__}: {exc}"[:200])
        end = _sim_time_ns()
        trace["cases"].append(
            {
                "name": case.name,
                "start_ns": start,
                "end_ns": end,
                "ok": outcome.ok,
                "expected": outcome.expected,
                "got": outcome.got,
                "cycle": outcome.cycle,
            }
        )
        if case.name == target:
            trace["found"] = True
            clk = getattr(cocotb.top, clock_name, None) if clock_name else None
            if clk is not None:
                for _ in range(max(0, after)):
                    await FallingEdge(clk)
            break
    trace["stop_ns"] = _sim_time_ns()
    path = Path(os.environ["VERITY_WAVE_TRACE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, separators=(",", ":")), encoding="utf-8")
