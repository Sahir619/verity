"""cocotb testbench for alu8 (locked: the agent never sees or edits this file).

Exactly one cocotb test (run_all) runs 24 named cases: 19 directed, then 5 seeded random. A case is a list of
(a, b, op) vectors. For every vector the testbench drives the inputs on the falling edge, then checks that the
outputs have NOT changed yet (registered, latency 1), clocks once, and compares (y, zero, carry) with
golden.golden_alu8. X or Z on an output is a failure. Three cases test reset and one tests input glitches
between edges.

Timing convention: inputs change and outputs are sampled only on the falling clock edge.
"""

import os
import random
from dataclasses import dataclass, field

import cocotb
import golden
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import PASSED, Outcome, Recorder, fmt_hex, read_int, reset_dut, run_cases, settle, start_clock

TOP = "alu8"
RANDOM_VECTORS = 48
EDGE_VALUES = (0, 1, 2, 127, 128, 129, 254, 255)


@dataclass
class Case:
    name: str
    inputs: str
    kind: str = "vectors"  # vectors | reset_clears | reset_holds | glitch
    vectors: list = field(default_factory=list)  # (a, b, op)


class Handles:
    def __init__(self, dut):
        self.clk = dut.clk
        self.rst_n = dut.rst_n
        self.a = dut.a
        self.b = dut.b
        self.op = dut.op
        self.y = dut.y
        self.zero = dut.zero
        self.carry = dut.carry


def fmt_out(out):
    y, zero, carry = out
    return f"y={fmt_hex(y, 8)} zero={'X' if zero is None else zero} carry={'X' if carry is None else carry}"


def fmt_vec(vec):
    a, b, op = vec
    return f"a={fmt_hex(a, 8)} b={fmt_hex(b, 8)} op={op} ({golden.OP_NAMES[op]})"


def read_out(h):
    return read_int(h.y), read_int(h.zero), read_int(h.carry)


def vectors_for(op, pairs):
    return [(a, b, op) for a, b in pairs]


def random_vector(rng):
    def value():
        return rng.choice(EDGE_VALUES) if rng.random() < 0.25 else rng.randrange(256)

    return value(), value(), rng.randrange(8)


def build_cases(rng):
    cases = []

    def add(name, text, vectors, kind="vectors"):
        cases.append(Case(name, text, kind, vectors))

    add("op_add", "op 0 on edge values and mixed pairs", vectors_for(0, [(0, 0), (1, 1), (100, 27), (255, 0), (170, 85), (127, 1), (128, 127), (200, 100)]))
    add("op_sub", "op 1 on edge values and mixed pairs", vectors_for(1, [(0, 0), (5, 3), (3, 5), (255, 255), (0, 1), (128, 1), (100, 200), (255, 0)]))
    add("op_and", "op 2 on masks", vectors_for(2, [(0xFF, 0x0F), (0xAA, 0x55), (0xF0, 0xF0), (0x00, 0xFF), (0xFF, 0xFF), (0x3C, 0xC3), (0xB7, 0x6D)]))
    add("op_or", "op 3 on masks", vectors_for(3, [(0xFF, 0x0F), (0xAA, 0x55), (0xF0, 0xF0), (0x00, 0x00), (0x00, 0xFF), (0x3C, 0xC3), (0xB7, 0x6D)]))
    add("op_xor", "op 4 on masks", vectors_for(4, [(0xFF, 0x0F), (0xAA, 0x55), (0xF0, 0xF0), (0x00, 0x00), (0xFF, 0xFF), (0x3C, 0xC3), (0xB7, 0x6D)]))
    add("op_shl", "op 5, shift amount in b[2:0]", vectors_for(5, [(0x01, 1), (0x81, 1), (0xFF, 4), (0xA5, 7), (0x80, 1), (0x12, 0), (0xC3, 2)]))
    add("op_shr", "op 6, shift amount in b[2:0]", vectors_for(6, [(0x80, 1), (0x81, 1), (0xFF, 4), (0xA5, 7), (0x01, 1), (0x12, 0), (0xC3, 2)]))
    add("op_cmp", "op 7 equal, less, greater", vectors_for(7, [(5, 5), (3, 9), (9, 3), (0, 0), (255, 0), (0, 255), (128, 127)]))
    add("add_carry_boundary", "op 0 around 255", vectors_for(0, [(255, 1), (128, 128), (255, 255), (254, 1), (127, 128), (0, 255)]))
    add("sub_borrow_boundary", "op 1 around 0", vectors_for(1, [(0, 255), (1, 2), (2, 1), (0, 0), (255, 255), (128, 129)]))
    add("shift_all_amounts", "a=0xA5, amounts 0 to 7, op 5 then op 6", [(0xA5, n, 5) for n in range(8)] + [(0xA5, n, 6) for n in range(8)])
    add("shift_high_bits_ignored", "only b[2:0] is the shift amount", [(0xA5, 0xF9, 5), (0xA5, 0x08, 5), (0xA5, 0x0B, 5), (0xA5, 0xF9, 6), (0xA5, 0x08, 6), (0xA5, 0x0B, 6)])
    add("zero_flag", "results that are and are not zero", [(5, 5, 1), (0, 0, 0), (0xF0, 0x0F, 2), (0xFF, 0xFF, 4), (0x80, 1, 5), (1, 1, 6), (1, 2, 0), (0xFF, 0x01, 0), (3, 5, 4)])
    add("carry_not_sticky", "carry set by add or sub, then cleared by logic ops", [(255, 1, 0), (6, 3, 2), (0, 1, 1), (7, 9, 3), (255, 255, 0), (1, 1, 4)])
    cases.append(Case("reset_clears_outputs", "nonzero result, then reset", "reset_clears"))
    cases.append(Case("reset_holds_outputs", "inputs change while reset is held low", "reset_holds"))
    cases.append(Case("latency_registered", "inputs glitch between clock edges", "glitch"))
    add("back_to_back_ops", "a different op every cycle", [(0xB6, 0x35, n % 8) for n in range(16)] + [(0x0F, 0xF1, (n * 3) % 8) for n in range(16)])
    add("opcode_sweep_fixed", "a=0xB6 b=0x35, all eight ops", [(0xB6, 0x35, n) for n in range(8)])
    for i in range(5):
        cases.append(Case(f"random_{i + 1}", f"{RANDOM_VECTORS} random vectors, seed {os.environ.get('VERITY_SEED', '1')}, set {i + 1}", "vectors", [random_vector(rng) for _ in range(RANDOM_VECTORS)]))
    return cases


async def clock_once(h):
    await RisingEdge(h.clk)
    await FallingEdge(h.clk)


def drive(h, a, b, op):
    h.a.value = a
    h.b.value = b
    h.op.value = op


async def run_vectors(h, vectors, prev):
    """Apply each vector for one clock edge. `prev` is the output tuple seen before the first vector."""
    for n, vec in enumerate(vectors, start=1):
        a, b, op = vec
        expected = golden.golden_alu8(a, b, op)
        drive(h, a, b, op)
        await settle(1)
        early = read_out(h)
        if early != prev:
            return Outcome(False, f"outputs unchanged before the edge: {fmt_out(prev)}", f"{fmt_out(early)} (changed before the edge)", n, fmt_vec(vec))
        await clock_once(h)
        got = read_out(h)
        if got != expected:
            return Outcome(False, fmt_out(expected), fmt_out(got), n, fmt_vec(vec))
        prev = got
    return PASSED


async def run_case(h, case):
    await reset_dut(h.clk, h.rst_n)
    zero_out = (0, 0, 0)
    after_reset = read_out(h)
    if after_reset != zero_out:
        return Outcome(False, fmt_out(zero_out) + " after reset", fmt_out(after_reset), 0)

    if case.kind == "vectors":
        return await run_vectors(h, case.vectors, zero_out)

    if case.kind == "reset_clears":
        outcome = await run_vectors(h, [(255, 1, 0)], zero_out)
        if not outcome.ok:
            return outcome
        await reset_dut(h.clk, h.rst_n)
        got = read_out(h)
        if got != zero_out:
            return Outcome(False, fmt_out(zero_out) + " after reset", fmt_out(got), 1)
        return PASSED

    if case.kind == "reset_holds":
        h.rst_n.value = 0
        for n, vec in enumerate([(255, 1, 0), (0x55, 0xAA, 4), (3, 3, 7), (0xF0, 0x0F, 2)], start=1):
            drive(h, *vec)
            await clock_once(h)
            got = read_out(h)
            if got != zero_out:
                h.rst_n.value = 1
                return Outcome(False, fmt_out(zero_out) + " while reset is low", fmt_out(got), n, fmt_vec(vec))
        h.rst_n.value = 1
        return PASSED

    # glitch: inputs change mid-cycle and come back; only the values at the edge may count.
    first = (200, 100, 0)  # y=0x2C zero=0 carry=1
    other = (9, 9, 4)  # y=0 zero=1 carry=0
    drive(h, *first)
    await clock_once(h)
    settled = read_out(h)
    if settled != golden.golden_alu8(*first):
        return Outcome(False, fmt_out(golden.golden_alu8(*first)), fmt_out(settled), 1, fmt_vec(first))
    drive(h, *other)
    await settle(2)
    mid = read_out(h)
    if mid != settled:
        return Outcome(False, f"outputs unchanged between edges: {fmt_out(settled)}", fmt_out(mid), 2, fmt_vec(other))
    drive(h, *first)
    await settle(2)
    await clock_once(h)
    got = read_out(h)
    if got != golden.golden_alu8(*first):
        return Outcome(False, fmt_out(golden.golden_alu8(*first)), fmt_out(got), 2, fmt_vec(first))
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    h = Handles(dut)
    start_clock(dut, "clk")
    drive(h, 0, 0, 0)
    h.rst_n.value = 1
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(h, case))
