"""The one generic cocotb testbench every custom block (CE3) uses. Never per-block: copied verbatim into any
custom pack at Approve time, same file, every time.

Reads /pack/task.toml for the port list, the clock and reset names, and /pack/cases.json for the cases (the
drafted golden model's own worked examples plus a few generated ones, already checked by /verity/bin/check_golden
before Approve). One clock edge per step, lockstep with one golden_step call: a block's latency lives entirely
inside its own golden_step state (CONTRACTS section 4), so this file never special-cases it. A purely
combinational pack (no clock) settles and samples the same step with no clock edge.

Timing convention (same as every locked pack): inputs change and outputs are sampled only on the falling edge,
except a combinational pack, which has no clock at all.
"""

import os
from pathlib import Path

import cocotb
import tomllib
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import PASSED, Outcome, Recorder, fmt_hex, read_int, run_cases, settle, start_clock

import golden


def _load_task() -> dict:
    path = Path(os.environ.get("VERITY_PACK_DIR", "/pack")) / "task.toml"
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _load_cases() -> list[dict]:
    path = Path(os.environ.get("VERITY_PACK_DIR", "/pack")) / "cases.json"
    import json

    return json.loads(path.read_text(encoding="utf-8"))["cases"]


TASK = _load_task()
TOP = TASK["top"]
PORTS = TASK["ports"]
IN_PORTS = [p for p in PORTS if p["dir"] == "in"]
OUT_PORTS = [p for p in PORTS if p["dir"] == "out"]
CLOCK_NAME = TASK.get("clock", {}).get("name", "")


async def run_case(dut, case: dict) -> Outcome:
    state: dict = {}
    for n, step in enumerate(case.get("steps", []), start=1):
        inputs = step.get("inputs", {})
        rst = bool(step.get("rst", False))
        for p in IN_PORTS:
            getattr(dut, p["name"]).value = int(inputs.get(p["name"], 0))
        await settle(1)
        if CLOCK_NAME:
            await RisingEdge(getattr(dut, CLOCK_NAME))
            await FallingEdge(getattr(dut, CLOCK_NAME))
        try:
            state, expected = golden.golden_step(state, inputs, rst)
        except Exception as exc:  # noqa: BLE001  (a draft that raises here already failed check_golden before
            # Approve; this guards a pack that somehow still has a bad golden.py from crashing the whole sim run)
            return Outcome(False, "golden_step runs without raising", f"{type(exc).__name__}: {exc}"[:200], n)
        got = {p["name"]: read_int(getattr(dut, p["name"])) for p in OUT_PORTS}
        mismatches = {k: (v, got.get(k)) for k, v in expected.items() if got.get(k) != v}
        if mismatches:
            exp_text = ", ".join(f"{k}={fmt_hex(v, _width_of(k))}" for k, (v, _) in mismatches.items())
            got_text = ", ".join(f"{k}={fmt_hex(g, _width_of(k))}" for k, (_, g) in mismatches.items())
            return Outcome(False, exp_text, got_text, n, f"step {n}")
    return PASSED


def _width_of(name: str) -> int:
    port = next((p for p in OUT_PORTS if p["name"] == name), None)
    return int(port["width"]) * int(port.get("count", 1)) if port else 1


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    if CLOCK_NAME:
        start_clock(dut, CLOCK_NAME)
    cases = _load_cases()
    await run_cases(rec, [_Case(c) for c in cases], lambda case: run_case(dut, case.raw))


class _Case:
    """Adapts a cases.json entry to the `.name`/`.inputs` shape run_cases expects for its own log lines."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.name = raw.get("name", "case")
        self.inputs = raw.get("name", "case")
