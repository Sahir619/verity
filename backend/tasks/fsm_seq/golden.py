"""Golden model for fsm_seq: serial detector for a bit pattern, overlaps optional, registered output.

Pure function of its inputs: no I/O beyond reading params.json, no cocotb, no randomness. The knob values (pattern,
overlap) come from params.json next to this file. It is a plain matcher over the bits received since reset, not a
state machine, so it is independent of the reference design.
"""

import json
from pathlib import Path

PARAMS = json.loads(Path(__file__).with_name("params.json").read_text(encoding="utf-8"))

PATTERN = tuple(int(ch) for ch in PARAMS["pattern"])
OVERLAP = PARAMS["overlap"]


def golden_fsm_seq_step(history, rst_n, din):
    """One rising edge. `history` is the tuple of recent usable bits (oldest first, at most len(PATTERN) - 1 of them).

    Returns (new_history, detect). Reset forgets every bit and clears detect. Without overlap a detection also
    forgets every bit, so the next pattern must start with a later bit.
    """
    if not rst_n:
        return (), 0
    window = (*history, din & 1)
    hit = window == PATTERN
    if hit and not OVERLAP:
        return (), 1
    return window[-(len(PATTERN) - 1):], 1 if hit else 0
