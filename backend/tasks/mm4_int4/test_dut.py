"""cocotb testbench for mm4_int4 (locked: the agent never sees or edits this file).

Exactly one cocotb test (run_all) runs 16 named cases: 11 directed, then 5 seeded random. Same protocol as
matmul4 with signed 4-bit lanes: every case resets the DUT, pulses start (edge 0), loads one row of A and B on
each of edges 1 to 4, and then checks:
  * done rises after exactly edge 24 (not earlier, not later), within VERITY_MAX_CYCLES;
  * all 16 signed 10-bit lanes of c equal golden.golden_mm4_int4 (X or Z counts as a failure);
  * c and done hold for three more edges, with random inputs and start pulses (start is ignored once done);
  * after reset, c and done are 0.

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
    pack_lanes,
    read_int,
    reset_dut,
    run_cases,
    start_clock,
    unpack_lanes,
)

TOP = "mm4_int4"
N = golden.N
WIDTH = golden.ELEM_BITS
RESULT_BITS = golden.RESULT_BITS
LATENCY = golden.CYCLES
MAX_CYCLES = env_int("VERITY_MAX_CYCLES", 64)
HOLD_EDGES = 3
MID_RESET_AFTER = 8


@dataclass
class Case:
    name: str
    a: list  # a[r] = row r of matrix A (four signed values)
    b: list  # b[r] = row r of matrix B
    inputs: str
    hold_start: bool = False  # keep start high for the whole case
    junk_first: bool = False  # random a_row and b_row on the start edge (must be ignored)
    mid_reset: bool = False  # reset in the middle of a first run, then do a clean run


class Handles:
    def __init__(self, dut):
        self.clk = dut.clk
        self.rst_n = dut.rst_n
        self.start = dut.start
        self.a_row = dut.a_row
        self.b_row = dut.b_row
        self.c = dut.c
        self.done = dut.done


def fmt_mat(rows):
    return "[" + ",".join("[" + ",".join(str(v) for v in row) + "]" for row in rows) + "]"


def describe(a, b):
    return f"A={fmt_mat(a)} B={fmt_mat(b)}"


def rand_row(rng):
    return [rng.randrange(-8, 8) for _ in range(N)]


def rand_mat(rng):
    return [rand_row(rng) for _ in range(N)]


def full(value):
    return [[value] * N for _ in range(N)]


def identity():
    return [[1 if i == j else 0 for j in range(N)] for i in range(N)]


def build_cases(rng):
    cases = []
    distinct_a = [[1, -2, 3, -4], [5, -6, 7, -8], [-1, 2, -3, 4], [-5, 6, -7, 0]]
    distinct_b = [[7, 6, -5, -4], [3, -2, 1, 0], [-8, 7, -6, 5], [4, -3, 2, -1]]
    single_a, single_b = full(0), full(0)
    single_a[1][2] = -3
    single_b[2][0] = 5
    checker = [[7 if (i + j) % 2 == 0 else -8 for j in range(N)] for i in range(N)]

    def add(name, a, b, **flags):
        cases.append(Case(name, a, b, describe(a, b), **flags))

    add("mm_zero", full(0), full(0))
    add("mm_identity_a", identity(), distinct_b)
    add("mm_all_min", full(-8), full(-8))
    add("mm_all_max", full(7), full(7))
    add("mm_min_times_max", full(-8), full(7))
    add("mm_mixed_signs", checker, distinct_a)
    add("mm_single_element", single_a, single_b)
    add("mm_asymmetric", distinct_a, distinct_b)
    add("mm_start_held_high", distinct_a, distinct_b, hold_start=True)
    add("mm_junk_on_start_edge", distinct_b, distinct_a, junk_first=True)
    add("mm_reset_mid_run", distinct_b, distinct_b, mid_reset=True)
    for i in range(5):
        a, b = rand_mat(rng), rand_mat(rng)
        cases.append(Case(f"mm_random_{i + 1}", a, b, f"random signed matrices, seed {os.environ.get('VERITY_SEED', '1')}, case {i + 1}"))
    return cases


def drive(h, *, start, a_lanes, b_lanes):
    h.start.value = start
    h.a_row.value = pack_lanes(a_lanes, WIDTH)
    h.b_row.value = pack_lanes(b_lanes, WIDTH)


async def idle_edge(h):
    await RisingEdge(h.clk)
    await FallingEdge(h.clk)


async def start_edge(h, case, rng):
    """Edge 0: start high (row inputs ignored by contract: junk or zero), then back to the case's start level."""
    junk_a, junk_b = (rand_row(rng), rand_row(rng)) if case.junk_first else ([0] * N, [0] * N)
    drive(h, start=1, a_lanes=junk_a, b_lanes=junk_b)
    await RisingEdge(h.clk)
    await FallingEdge(h.clk)
    h.start.value = 1 if case.hold_start else 0


def mismatch(got_flat, expected, cycle):
    if got_flat is None:
        return Outcome(False, "16 result lanes", "X", cycle)
    got = [golden.to_signed(v, RESULT_BITS) for v in unpack_lanes(got_flat, RESULT_BITS, N * N)]
    bad = [i for i in range(N * N) if got[i] != expected[i]]
    first = bad[0]
    return Outcome(
        False,
        f"c[{first}]={expected[first]}",
        f"c[{first}]={got[first]} ({len(bad)} of {N * N} lanes differ)",
        cycle,
    )


async def run_case(h, case, rng):
    expected = golden.golden_mm4_int4(case.a, case.b)
    expected_flat = pack_lanes(expected, RESULT_BITS)
    await reset_dut(h.clk, h.rst_n)
    if case.mid_reset:
        drive(h, start=0, a_lanes=[0] * N, b_lanes=[0] * N)
        await idle_edge(h)
        await start_edge(h, case, rng)
        for _ in range(MID_RESET_AFTER):
            drive(h, start=0, a_lanes=rand_row(rng), b_lanes=rand_row(rng))
            await idle_edge(h)
        await reset_dut(h.clk, h.rst_n)

    c0, done0 = read_int(h.c), read_int(h.done)
    if c0 != 0 or done0 != 0:
        got = f"c={'X' if c0 is None else ('0' if c0 == 0 else 'nonzero')} done={'X' if done0 is None else done0}"
        return Outcome(False, "c=0 done=0 after reset", got, 0)

    drive(h, start=0, a_lanes=[0] * N, b_lanes=[0] * N)
    await idle_edge(h)
    await start_edge(h, case, rng)

    done_at = None
    for k in range(1, MAX_CYCLES + 1):
        a_lanes, b_lanes = (case.a[k - 1], case.b[k - 1]) if k <= N else ([0] * N, [0] * N)
        h.a_row.value = pack_lanes(a_lanes, WIDTH)
        h.b_row.value = pack_lanes(b_lanes, WIDTH)
        await idle_edge(h)
        done = read_int(h.done)
        if done is None:
            return Outcome(False, "done is 0 or 1", "X", k)
        if done == 1:
            done_at = k
            break
    if done_at is None:
        return Outcome(False, f"done at cycle {LATENCY}", f"no done after {MAX_CYCLES} cycles", MAX_CYCLES)
    if done_at != LATENCY:
        return Outcome(False, f"done at cycle {LATENCY}", f"done at cycle {done_at}", done_at)

    c_flat = read_int(h.c)
    if c_flat != expected_flat:
        return mismatch(c_flat, expected, LATENCY)

    for j in range(1, HOLD_EDGES + 1):
        drive(
            h,
            start=1 if case.hold_start else rng.getrandbits(1),
            a_lanes=rand_row(rng),
            b_lanes=rand_row(rng),
        )
        await idle_edge(h)
        c_now, done_now = read_int(h.c), read_int(h.done)
        if done_now != 1 or c_now != expected_flat:
            return Outcome(False, "c and done held until reset", "c or done changed after done", LATENCY + j)
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    h = Handles(dut)
    start_clock(dut, "clk")
    drive(h, start=0, a_lanes=[0] * N, b_lanes=[0] * N)
    h.rst_n.value = 1
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(h, case, rng))
