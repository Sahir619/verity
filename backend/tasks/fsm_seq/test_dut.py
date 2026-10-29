"""cocotb testbench for fsm_seq (locked: the agent never sees or edits this file).

The pattern and the overlap setting come from golden.py (params.json). Exactly one cocotb test (run_all) runs 12 named cases: 10 directed, then 2 seeded random. A case is a list of
(rst_n, din) stimuli, one per clock edge, checked against golden.golden_fsm_seq_step. For every step the
testbench drives the inputs on the falling edge, checks detect has NOT changed yet (registered), clocks once,
then compares detect. X or Z on detect is a failure.

Timing convention: inputs change and outputs are sampled only on the falling clock edge.
"""

import os
import random
from dataclasses import dataclass

import cocotb
import golden
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import PASSED, Outcome, Recorder, read_int, reset_dut, run_cases, settle, start_clock

TOP = "fsm_seq"
RANDOM_STEPS = 96
PATTERN = "".join(str(b) for b in golden.PATTERN)  # from params.json
N = len(PATTERN)


@dataclass
class Case:
    name: str
    inputs: str
    steps: list  # (rst_n, din)


def bits(text):
    return [(1, int(ch)) for ch in text]


def border_len(text):
    """Longest proper prefix of text that is also a suffix (0: the pattern cannot overlap itself)."""
    return next((k for k in range(len(text) - 1, 0, -1) if text[:k] == text[-k:]), 0)


def flip(text, index):
    return text[:index] + ("1" if text[index] == "0" else "0") + text[index + 1 :]


def random_stream(rng):
    """Random bits with the pattern, near misses and overlapping continuations mixed in, so matches really occur."""
    ext = PATTERN[border_len(PATTERN) :]
    steps = []
    while len(steps) < RANDOM_STEPS:
        pick = rng.random()
        if pick < 0.35:
            chunk = PATTERN
        elif pick < 0.5:
            chunk = flip(PATTERN, rng.randrange(N))
        elif pick < 0.65:
            chunk = ext
        else:
            chunk = "".join(str(rng.getrandbits(1)) for _ in range(rng.randint(1, 3)))
        for ch in chunk:
            steps.append((0 if rng.random() < 0.03 else 1, int(ch)))
    return steps[:RANDOM_STEPS]


def build_cases(rng):
    ext = PATTERN[border_len(PATTERN) :]
    filler = "0" if "1" in PATTERN else "1"
    cases = [
        Case("reset_state", "reset held low with din high", [(0, 1), (0, 1), (0, 0), (1, 0), (1, 0)]),
        Case("exact_pattern", f"bits {PATTERN}", bits(PATTERN) + bits("0")),
        Case("pattern_twice", f"bits {PATTERN} twice back to back", bits(PATTERN * 2) + bits("0")),
        Case("overlapping_chain", f"bits {PATTERN + ext * 3}", bits(PATTERN + ext * 3) + bits("0")),
        Case("no_match_filler", f"{N + 4} bits of {filler}", bits(filler * (N + 4))),
        Case("near_miss_last_bit", f"bits {flip(PATTERN, N - 1)} twice, then {PATTERN}", bits(flip(PATTERN, N - 1) * 2 + PATTERN)),
        Case("near_miss_prefix", f"bits {PATTERN[:-1]} three times, then {PATTERN}", bits(PATTERN[:-1] * 3 + PATTERN)),
        Case(
            "reset_mid_pattern",
            f"bits {PATTERN[:-1]}, reset, last bit, then {PATTERN}",
            bits(PATTERN[:-1]) + [(0, int(PATTERN[-1]))] + bits(PATTERN[-1]) + bits(PATTERN),
        ),
        Case("reset_after_match", f"bits {PATTERN}, reset, bits {ext}", bits(PATTERN) + [(0, 0)] + bits(ext) + bits(PATTERN)),
        Case("all_ones", f"{2 * N} ones", bits("1" * (2 * N))),
    ]
    for i in range(2):
        cases.append(Case(f"random_{i + 1}", f"{RANDOM_STEPS} random steps, seed {os.environ.get('VERITY_SEED', '1')}, set {i + 1}", random_stream(rng)))
    return cases


def fmt_detect(value):
    return "detect=X" if value is None else f"detect={value}"


async def run_case(dut, case):
    await reset_dut(dut.clk, dut.rst_n)
    start = read_int(dut.detect)
    if start != 0:
        return Outcome(False, "detect=0 after reset", fmt_detect(start), 0)
    history, expected = (), 0
    for n, (rst_n, din) in enumerate(case.steps, start=1):
        dut.rst_n.value = rst_n
        dut.din.value = din
        await settle(1)
        early = read_int(dut.detect)
        if early != expected:
            return Outcome(
                False,
                f"detect unchanged before the edge: {expected}",
                f"{fmt_detect(early)} (changed before the edge)",
                n,
                f"step {n}: rst_n={rst_n} din={din}",
            )
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        history, expected = golden.golden_fsm_seq_step(history, rst_n, din)
        got = read_int(dut.detect)
        if got != expected:
            sent = "".join(str(d) for _, d in case.steps[:n])
            return Outcome(False, f"detect={expected}", fmt_detect(got), n, f"step {n}: rst_n={rst_n} din={din}, bits so far ...{sent[-24:]}")
    dut.rst_n.value = 1
    dut.din.value = 0
    return PASSED


@cocotb.test()
async def run_all(dut):
    rec = Recorder(TOP)
    start_clock(dut, "clk")
    dut.rst_n.value = 1
    dut.din.value = 0
    rng = random.Random(int(os.environ.get("VERITY_SEED", "1")))
    cases = build_cases(rng)
    await run_cases(rec, cases, lambda case: run_case(dut, case))
