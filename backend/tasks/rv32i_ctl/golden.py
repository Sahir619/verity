"""Golden model for rv32i_ctl. Written from the CPU contract, not from reference.v.

Clocked Mealy FSM. The shared testbench samples outputs after the rising edge with the
inputs still held, so the returned outputs are those of the state just entered.

The state register uses 0 FETCH, 1 DECODE, 2 EXEC, 3 MEM, 4 WB and 5 HALT.
Codes 6 and 7 also halt: only the halted flag is high, the state port echoes the code,
and the state that follows is 5. Reset (rst true, or rst_n low) goes to FETCH and wins over the table.
"""

from __future__ import annotations

FETCH, DECODE, EXEC, MEM, WB, HALT = 0, 1, 2, 3, 4, 5

_OUT_NAMES = ("state", "mem_valid", "mem_we", "ir_we", "rf_wen", "pc_we", "pc_sel", "halted")


def _bit(inputs: dict, name: str) -> int:
    return int(inputs.get(name, 0)) & 1


def _resetting(inputs: dict, rst: bool) -> bool:
    return bool(rst) or _bit(inputs, "rst_n") == 0


def _blank(cur: int) -> dict[str, int]:
    return {name: (cur if name == "state" else 0) for name in _OUT_NAMES}


def outputs_of(cur: int, inputs: dict) -> dict[str, int]:
    """Control outputs of a state code, using the inputs held on the edge that entered it."""
    cur &= 7
    out = _blank(cur)
    if cur == FETCH:
        out["mem_valid"] = 1
        out["ir_we"] = _bit(inputs, "mem_ready")
    elif cur == MEM:
        out["mem_valid"] = 1
        out["mem_we"] = _bit(inputs, "is_store")
    elif cur == WB:
        out["rf_wen"] = 1
        out["pc_we"] = 1
        taken_branch = _bit(inputs, "is_branch") and _bit(inputs, "taken")
        out["pc_sel"] = int(_bit(inputs, "is_jal") or _bit(inputs, "is_jalr") or taken_branch)
    elif cur in (HALT, 6, 7):
        out["halted"] = 1
    return out


def next_state(cur: int, inputs: dict) -> int:
    cur &= 7
    if cur in (HALT, 6, 7):
        return HALT
    if cur == FETCH:
        return DECODE if _bit(inputs, "mem_ready") else FETCH
    if cur == DECODE:
        return HALT if _bit(inputs, "trap") else EXEC
    if cur == EXEC:
        if _bit(inputs, "is_load") or _bit(inputs, "is_store"):
            return HALT if _bit(inputs, "misaligned") else MEM
        return WB
    if cur == MEM:
        return WB if _bit(inputs, "mem_ready") else MEM
    if cur == WB:
        return FETCH
    return HALT


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    cur = int(state.get("state", FETCH)) & 7
    nxt = FETCH if _resetting(inputs, rst) else next_state(cur, inputs)
    return {"state": nxt}, outputs_of(nxt, inputs)
