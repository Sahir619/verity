"""Golden model for mac4: LANES-lane unsigned LANE_BITS-bit multiply-accumulate over CYCLES accumulate edges.

Pure function of its inputs: no I/O beyond reading params.json, no cocotb, no randomness. The testbench (test_dut.py)
calls it and compares. The knob values (lanes, width, cycles) come from params.json next to this file.
"""

import json
from pathlib import Path

PARAMS = json.loads(Path(__file__).with_name("params.json").read_text(encoding="utf-8"))

LANES = PARAMS["lanes"]
LANE_BITS = PARAMS["width"]
CYCLES = PARAMS["cycles"]
ACC_BITS = 2 * LANE_BITS + (LANES * CYCLES - 1).bit_length()  # 2*W + ceil(log2(LANES*CYCLES))
ACC_MASK = (1 << ACC_BITS) - 1
LANE_MASK = (1 << LANE_BITS) - 1


def lane_products(a_lanes, b_lanes):
    """The unsigned products for one accumulate edge, one per lane."""
    return [(a & LANE_MASK) * (b & LANE_MASK) for a, b in zip(a_lanes, b_lanes)]


def golden_mac4(a_cycles, b_cycles):
    """Final acc after CYCLES accumulate edges.

    a_cycles[k] and b_cycles[k] are the lane values present on a and b at accumulate edge k + 1.
    The inputs on the start edge (edge 0) are not part of the sum.
    """
    acc = 0
    for a_lanes, b_lanes in zip(a_cycles, b_cycles):
        acc += sum(lane_products(a_lanes, b_lanes))
    return acc & ACC_MASK
