"""Golden model for add8: WIDTH-bit adder with carry in and carry out (combinational).

Pure function of its inputs: no I/O beyond reading params.json, no cocotb, no randomness. The knob value (width)
comes from params.json next to this file.
"""

import json
from pathlib import Path

PARAMS = json.loads(Path(__file__).with_name("params.json").read_text(encoding="utf-8"))

WIDTH = PARAMS["width"]
MASK = (1 << WIDTH) - 1


def golden_add8(a, b, cin):
    """Return (sum, cout): the low WIDTH bits of a + b + cin, and the carry out of the top bit."""
    total = (a & MASK) + (b & MASK) + (cin & 1)
    return total & MASK, total >> WIDTH
