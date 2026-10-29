"""cocotb testbench for cnt8 (locked: the agent never sees or edits this file).

The width and the optional en input come from golden.py (params.json). Exactly one cocotb test (run_all) runs 10 named cases: 7 directed, then 3 seeded random. A case is a list of
(rst_n, en) stimuli, one per clock edge, checked against golden.golden_cnt8_step. For every step the testbench
drives the inputs on the falling edge, checks q has NOT changed yet (registered), clocks once, then compares q.
X or Z on q is a failure.

Timing convention: inputs change and outputs are sampled only on the falling clock edge.
"""

import os
import random
from dataclasses import dataclass

import cocotb
import golden
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import PASSED, Outcome, Recorder, fmt_hex, read_int, reset_dut, run_cases, settle, start_clock

TOP = "cnt8"
WIDTH = golden.WIDTH
ENABLE = golden.ENABLE  # False: no en port, the counter is free-running
MAXV = golden.MASK
RANDOM_STEPS = 80
WRAP_EDGES = golden.WRAP_EDGES  # a full wrap at every width (no preload is possible); rendered by family.py with max_cycles_per_case


@dataclass
class Case:
    name: str
    inputs: str
    steps: list  # (rst_n, en)


def build_cases(rng):
    wrap = Case(f"wrap_at_{MAXV}", f"en high for {WRAP_EDGES} edges", [(1, 1)] * WRAP_EDGES)
    if ENABLE:
        hold = Case("hold_when_disabled", "count 3, then en low for 4 edges", [(1, 1)] * 3 + [(1, 0)] * 4)
        alternate = Case("enable_every_other", "en alternates for 12 edges", [(1, i % 2) for i in range(12)])
    else:
        hold = Case("reset_then_count", "count 3, reset once, count 4", [(1, 1)] * 3 + [(0, 1)] + [(1, 1)] * 4)
        alternate = Case("reset_every_fifth", "reset every fifth edge for 12 edges", [(0 if i % 5 == 4 else 1, 1) for i in range(12)])
    cases = [
        Case("reset_value", "reset held low, en low then high", [(0, 0), (0, 0), (0, 1), (0, 1)]),
        Case("count_up", "en high for 5 edges", [(1, 1)] * 5),
        hold,
        alternate,
        wrap,
        Case("reset_mid_count", "count 7, reset, count 3", [(1, 1)] * 7 + [(0, 0)] + [(1, 1)] * 3),
        Case("reset_overrides_enable", "en high while reset is low", [(1, 1)] * 3 + [(0, 1)] * 3 + [(1, 1)] * 2),
    ]
    for i in range(3):
        steps = [(0 if rng.random() < 0.05 else 1, 1 if rng.random() < 0.7 else 0) for _ in range(RANDOM_STEPS)]
        cases.append(Case(f"random_{i + 1}", f"{RANDOM_STEPS} random steps, seed {os.environ.get('VERITY_SEED', '1')}, set {i + 1}", steps))
    return cases


async def run_case(dut, case):
    await reset_dut(dut.clk, dut.rst_n)
    q = read_int(dut.q)
    if q != 0:
        return Outcome(False, f"q={fmt_hex(0, WIDTH)} after reset", f"q={fmt_hex(q, WIDTH)}", 0)
    expected_q = 0
    for n, (rst_n, en) in enumerate(case.steps, start=1):
        if not ENABLE:
            en = 1  # no en port: the counter always counts
        dut.rst_n.value = rst_n
        if ENABLE:
            dut.en.value = en
        await settle(1)
        early = read_int(dut.q)
        if early != expected_q:
            return Outcome(
                False,
                f"q unchanged before the edge: {fmt_hex(expected_q, WIDTH)}",
                f"q={fmt_hex(early, WIDTH)} (changed before the edge)",
                n,
                f"step {n}: rst_n={rst_n} en={en}",
            )
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        expected_q = golden.golden_cnt8_step(expected_q, rst_n, en)
        got = read_int(dut.q)
        if got != expected_q:
            return Outcome(False, f"q={fmt_hex(expected_q, WIDTH)}", f"q={fmt_hex(got, WIDTH)}", n, f"step {n}: rst_n={rst_n} en={en}")
    dut.rst_n.value = 1
    if ENABLE:
        dut.en.value = 0
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    start_clock(dut, "clk")
    dut.rst_n.value = 1
    if ENABLE:
        dut.en.value = 0
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(dut, case))
