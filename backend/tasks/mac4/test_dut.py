"""cocotb testbench for mac4 (locked: the agent never sees or edits this file).

Exactly one cocotb test (run_all) runs 16 named cases: 11 directed, then 5 seeded random. Every case resets the
DUT, starts a run, drives the per-cycle inputs from golden.py's point of view, and checks:
  * done rises after exactly edge 16 (not earlier, not later), within VERITY_MAX_CYCLES;
  * acc equals golden.golden_mac4 (X or Z counts as a failure);
  * acc and done hold for three more edges, with random inputs and start pulses (start is ignored once done);
  * after reset, acc and done are 0.

Timing convention: inputs change and outputs are sampled only on the falling clock edge.
"""

import os
import random
from dataclasses import dataclass

import cocotb
import golden
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import (
    PASSED,
    Outcome,
    Recorder,
    env_int,
    fmt_hex,
    fmt_lanes,
    pack_lanes,
    read_int,
    reset_dut,
    run_cases,
    start_clock,
)

TOP = "mac4"
LANES = golden.LANES
WIDTH = golden.LANE_BITS
CYCLES = golden.CYCLES
ACC_BITS = golden.ACC_BITS
MASK = (1 << WIDTH) - 1
MAX_CYCLES = env_int("VERITY_MAX_CYCLES", 64)
HOLD_EDGES = 3
MID_RESET_AFTER = 5


@dataclass
class Case:
    name: str
    a: list  # a[k] = four lane values on a at accumulate edge k + 1
    b: list
    inputs: str
    hold_start: bool = False  # keep start high for the whole case
    junk_first: bool = False  # random a and b on the start edge (they must be ignored)
    mid_reset: bool = False  # reset in the middle of a first run, then do a clean run


class Handles:
    def __init__(self, dut):
        self.clk = dut.clk
        self.rst_n = dut.rst_n
        self.start = dut.start
        self.a = dut.a
        self.b = dut.b
        self.acc = dut.acc
        self.done = dut.done


def const(a_lanes, b_lanes):
    return [list(a_lanes) for _ in range(CYCLES)], [list(b_lanes) for _ in range(CYCLES)]


def random_lanes(rng):
    return [rng.randrange(1 << WIDTH) for _ in range(LANES)]


def build_cases(rng):
    cases = []

    def add_const(name, a, b, **flags):
        ac, bc = const(a, b)
        cases.append(Case(name, ac, bc, f"a={fmt_lanes(a)} b={fmt_lanes(b)} x{CYCLES}", **flags))

    zeros = [0] * LANES
    top_bit = 1 << (WIDTH - 2)
    add_const("acc_zero", zeros, zeros)
    add_const("acc_16_cycles_max", [MASK] * LANES, [MASK] * LANES)
    add_const("acc_carry_boundary", [top_bit] + [0] * (LANES - 1), [top_bit] + [0] * (LANES - 1))
    add_const("acc_lane0_only", [200 & MASK] + [0] * (LANES - 1), [100 & MASK] + [0] * (LANES - 1))
    add_const("acc_lane_isolation", [(i + 1) & MASK for i in range(LANES)], [(i + 5) & MASK for i in range(LANES)])
    add_const("acc_lane3_only", [0] * (LANES - 1) + [MASK], [0] * (LANES - 1) + [MASK])
    add_const("acc_b_zero", [MASK] * LANES, zeros)

    a_var = [[(k * 17 + lane * 5 + 3) % (1 << WIDTH) for lane in range(LANES)] for k in range(CYCLES)]
    b_var = [[(k * 29 + lane * 11 + 7) % (1 << WIDTH) for lane in range(LANES)] for k in range(CYCLES)]
    cases.append(Case("acc_changing_inputs", a_var, b_var, f"different a and b every cycle (formula), x{CYCLES}"))

    add_const("acc_start_held_high", [(9 - i) & MASK for i in range(LANES)], [(i + 1) & MASK for i in range(LANES)], hold_start=True)
    add_const("acc_junk_on_start_edge", [(10 * (i + 1)) & MASK for i in range(LANES)], [1] * LANES, junk_first=True)
    add_const("acc_reset_mid_run", [(250 + i) & MASK for i in range(LANES)], [3 & MASK] * LANES, mid_reset=True)

    for i in range(5):
        a_cycles = [random_lanes(rng) for _ in range(CYCLES)]
        b_cycles = [random_lanes(rng) for _ in range(CYCLES)]
        cases.append(Case(f"acc_random_{i + 1}", a_cycles, b_cycles, f"random lanes every cycle, seed {os.environ.get('VERITY_SEED', '1')}, x{CYCLES}"))
    return cases


def drive(h, *, start, a_lanes, b_lanes):
    h.start.value = start
    h.a.value = pack_lanes(a_lanes, WIDTH)
    h.b.value = pack_lanes(b_lanes, WIDTH)


async def idle_edge(h):
    await RisingEdge(h.clk)
    await FallingEdge(h.clk)


async def start_edge(h, case, rng):
    """Edge 0: start high (inputs ignored by contract: junk or zero), then back to the case's start level."""
    junk_a, junk_b = (random_lanes(rng), random_lanes(rng)) if case.junk_first else ([0] * LANES, [0] * LANES)
    drive(h, start=1, a_lanes=junk_a, b_lanes=junk_b)
    await RisingEdge(h.clk)
    await FallingEdge(h.clk)
    h.start.value = 1 if case.hold_start else 0


async def run_case(h, case, rng):
    expected = golden.golden_mac4(case.a, case.b)
    expected_hex = fmt_hex(expected, ACC_BITS)
    await reset_dut(h.clk, h.rst_n)
    if case.mid_reset:
        drive(h, start=0, a_lanes=[0] * LANES, b_lanes=[0] * LANES)
        await idle_edge(h)
        await start_edge(h, case, rng)
        for _ in range(MID_RESET_AFTER):
            drive(h, start=0, a_lanes=random_lanes(rng), b_lanes=random_lanes(rng))
            await idle_edge(h)
        await reset_dut(h.clk, h.rst_n)

    acc0, done0 = read_int(h.acc), read_int(h.done)
    if acc0 != 0 or done0 != 0:
        got = f"acc={fmt_hex(acc0, ACC_BITS)} done={'X' if done0 is None else done0}"
        return Outcome(False, f"acc={fmt_hex(0, ACC_BITS)} done=0 after reset", got, 0)

    drive(h, start=0, a_lanes=[0] * LANES, b_lanes=[0] * LANES)
    await idle_edge(h)
    await start_edge(h, case, rng)

    done_at = None
    for k in range(1, MAX_CYCLES + 1):
        a_lanes, b_lanes = (case.a[k - 1], case.b[k - 1]) if k <= CYCLES else (random_lanes(rng), random_lanes(rng))
        h.a.value = pack_lanes(a_lanes, WIDTH)
        h.b.value = pack_lanes(b_lanes, WIDTH)
        await idle_edge(h)
        done = read_int(h.done)
        if done is None:
            return Outcome(False, "done is 0 or 1", "X", k)
        if done == 1:
            done_at = k
            break
    if done_at is None:
        return Outcome(False, f"done at cycle {CYCLES}", f"no done after {MAX_CYCLES} cycles", MAX_CYCLES)
    if done_at != CYCLES:
        return Outcome(False, f"done at cycle {CYCLES}", f"done at cycle {done_at}", done_at)

    acc = read_int(h.acc)
    if acc is None:
        return Outcome(False, expected_hex, "X", CYCLES)
    if acc != expected:
        return Outcome(False, expected_hex, fmt_hex(acc, ACC_BITS), CYCLES)

    for j in range(1, HOLD_EDGES + 1):
        drive(
            h,
            start=1 if case.hold_start else rng.getrandbits(1),
            a_lanes=random_lanes(rng),
            b_lanes=random_lanes(rng),
        )
        await idle_edge(h)
        acc_now, done_now = read_int(h.acc), read_int(h.done)
        if done_now != 1 or acc_now != expected:
            got = f"acc={fmt_hex(acc_now, ACC_BITS)} done={'X' if done_now is None else done_now}"
            return Outcome(False, f"acc={expected_hex} done=1 held until reset", got, CYCLES + j)
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    h = Handles(dut)
    start_clock(dut, "clk")
    drive(h, start=0, a_lanes=[0] * LANES, b_lanes=[0] * LANES)
    h.rst_n.value = 1
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(h, case, rng))
