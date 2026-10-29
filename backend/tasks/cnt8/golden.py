"""Golden model for cnt8: WIDTH-bit up counter, optional enable, synchronous active-low reset.

Pure function of its inputs: no I/O beyond reading params.json, no cocotb, no randomness. The knob values (width,
enable) come from params.json next to this file. Without the enable input the counter is free-running: pass en=1.
"""

import json
from pathlib import Path

PARAMS = json.loads(Path(__file__).with_name("params.json").read_text(encoding="utf-8"))

WIDTH = PARAMS["width"]
ENABLE = PARAMS["enable"]
MASK = (1 << WIDTH) - 1
WRAP_EDGES = PARAMS["wrap_edges"]  # edges the testbench clocks to show a full wrap; rendered by family.py


def golden_cnt8_step(q, rst_n, en):
    """Value of q after one rising edge that samples rst_n and en. Reset wins over enable."""
    if not rst_n:
        return 0
    return (q + 1) & MASK if en else q & MASK
