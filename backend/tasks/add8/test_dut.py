"""cocotb testbench for add8 (locked: the agent never sees or edits this file).

The width comes from golden.WIDTH (params.json). Exactly one cocotb test (run_all) runs 12 named cases: 9 directed,
then 3 seeded random. A case is a list of (a, b, cin) vectors. The design is combinational, so each vector is driven and sampled 1 ns later, with no clock.
X or Z on an output is a failure.
"""

import os
import random
from dataclasses import dataclass

import cocotb
import golden
from verity_tb import PASSED, Outcome, Recorder, fmt_hex, read_int, run_cases, settle

TOP = "add8"
WIDTH = golden.WIDTH
MAXV = golden.MASK
HALF = 1 << (WIDTH - 1)
ALT_A = sum(1 << i for i in range(1, WIDTH, 2))  # 0b1010... (0xAA at 8 bits)
ALT_B = sum(1 << i for i in range(0, WIDTH, 2))  # 0b0101... (0x55 at 8 bits)
RANDOM_VECTORS = 64
CORNERS = tuple(sorted({0, 1, HALF - 1, HALF, MAXV}))


def fit(value):
    """A directed value written for 8 bits, kept inside WIDTH bits (unchanged when WIDTH >= 8)."""
    return min(value, MAXV)


@dataclass
class Case:
    name: str
    inputs: str
    vectors: list  # (a, b, cin)


def build_cases(rng):
    cases = [
        Case("add_zero", "0 + 0 + 0", [(0, 0, 0)]),
        Case("add_one_plus_one", "1 + 1", [(1, 1, 0), (2, 3, 0), (fit(10), fit(20), 0)]),
        Case("add_carry_in_only", "0 + 0 + cin", [(0, 0, 1), (1, 0, 1), (MAXV - 1, 0, 1)]),
        Case("add_max_no_cin", f"{MAXV} + 0", [(MAXV, 0, 0), (0, MAXV, 0), (MAXV, 1, 0)]),
        Case("add_max_with_cin", f"{MAXV} + 0 + 1", [(MAXV, 0, 1), (0, MAXV, 1), (MAXV, 1, 1)]),
        Case("add_overflow", f"{MAXV} + {MAXV} + cin", [(MAXV, MAXV, 0), (MAXV, MAXV, 1), (fit(200), fit(100), 1)]),
        Case("add_half_range", f"{HALF} + {HALF}", [(HALF, HALF, 0), (HALF, HALF - 1, 0), (HALF - 1, HALF, 1)]),
        Case("add_alternating_bits", f"{fmt_hex(ALT_A, WIDTH)} + {fmt_hex(ALT_B, WIDTH)}", [(ALT_A, ALT_B, 0), (ALT_A, ALT_B, 1), (ALT_B, ALT_B, 0)]),
        Case("add_corner_grid", "corner values for a, b and cin", [(a, b, c) for a in CORNERS for b in CORNERS for c in (0, 1)]),
    ]
    for i in range(3):
        vectors = [(rng.randrange(1 << WIDTH), rng.randrange(1 << WIDTH), rng.getrandbits(1)) for _ in range(RANDOM_VECTORS)]
        cases.append(Case(f"add_random_{i + 1}", f"{RANDOM_VECTORS} random vectors, seed {os.environ.get('VERITY_SEED', '1')}, set {i + 1}", vectors))
    return cases


def fmt_out(total, cout):
    return f"sum={fmt_hex(total, WIDTH)} cout={'X' if cout is None else cout}"


async def run_case(dut, case):
    for n, (a, b, cin) in enumerate(case.vectors, start=1):
        expected = golden.golden_add8(a, b, cin)
        dut.a.value = a
        dut.b.value = b
        dut.cin.value = cin
        await settle(1)
        got = (read_int(dut.sum), read_int(dut.cout))
        if got != expected:
            stimulus = f"a={fmt_hex(a, WIDTH)} b={fmt_hex(b, WIDTH)} cin={cin}"
            return Outcome(False, fmt_out(*expected), fmt_out(*got), n, stimulus)
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    dut.a.value = 0
    dut.b.value = 0
    dut.cin.value = 0
    await settle(1)
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(dut, case))
