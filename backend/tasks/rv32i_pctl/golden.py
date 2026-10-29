"""Golden model for rv32i_pctl. Written from the CPU contract, not from reference.v.

Two registers: ex_valid and halted, both 0 after reset. The shared testbench samples
outputs after the rising edge with the inputs still held, so the returned outputs are
those of the state just entered.

trap_now and redirect used to compute the next registers are those of the state being
left. The dict this function returns is recomputed from the state just entered.
"""

from __future__ import annotations


def _bit(inputs: dict, name: str) -> int:
    return int(inputs.get(name, 0)) & 1


def _resetting(inputs: dict, rst: bool) -> bool:
    return bool(rst) or _bit(inputs, "rst_n") == 0


def outputs_of(ex_valid: int, halted: int, inputs: dict) -> dict[str, int]:
    """Control outputs of one state, using the inputs held on the edge that entered it."""
    valid = ex_valid & 1
    stopped = halted & 1
    busy = valid & (1 - stopped)
    load = _bit(inputs, "is_load")
    store = _bit(inputs, "is_store")
    memory = load | store
    bad_addr = memory & _bit(inputs, "misaligned")
    trap_now = busy & (_bit(inputs, "trap") | bad_addr)
    running = busy & (1 - trap_now)
    taken_branch = _bit(inputs, "is_branch") & _bit(inputs, "taken")
    jump = _bit(inputs, "is_jal") | _bit(inputs, "is_jalr") | taken_branch
    redirect = running & jump
    dmem_valid = running & memory
    if stopped:
        stall = 3
    elif valid:
        stall = 0
    else:
        stall = 1
    return {
        "ex_valid": valid,
        "halted": stopped,
        "advance": (1 - stopped) & (1 - trap_now),
        "redirect": redirect,
        "rf_wen": running,
        "dmem_valid": dmem_valid,
        "dmem_we": dmem_valid & store,
        "retire": busy,
        "trap_now": trap_now,
        "dbg_stall": stall,
    }


def next_regs(ex_valid: int, halted: int, inputs: dict) -> tuple[int, int]:
    """Registers loaded by the rising edge, from the state being left."""
    leaving = outputs_of(ex_valid, halted, inputs)
    stopped = (halted & 1) | leaving["trap_now"]
    valid = (1 - stopped) & (1 - leaving["redirect"])
    return valid, stopped


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    cur_valid = int(state.get("ex_valid", 0)) & 1
    cur_halt = int(state.get("halted", 0)) & 1
    if _resetting(inputs, rst):
        nxt_valid, nxt_halt = 0, 0
    else:
        nxt_valid, nxt_halt = next_regs(cur_valid, cur_halt, inputs)
    return {"ex_valid": nxt_valid, "halted": nxt_halt}, outputs_of(nxt_valid, nxt_halt, inputs)
