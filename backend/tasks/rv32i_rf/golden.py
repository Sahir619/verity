"""Golden model for rv32i_rf. Written from the CPU contract, not from reference.v.

Clocked block. The shared testbench drives the inputs, takes one rising edge, then samples
the outputs with those inputs still held. golden_step therefore returns the register file
after that edge, and the reads of that new state.

x0 is not stored. A read of address 0 is 0. A write of address 0 is ignored. rst_n low
(or rst true) clears x1 to x31 and wins over we.
"""

from __future__ import annotations


def _u(value: object, bits: int) -> int:
    return int(value) & ((1 << bits) - 1)


def _regs_of(state: dict) -> list[int]:
    raw = state.get("regs")
    regs = [0] * 32
    if isinstance(raw, list) and len(raw) == 32:
        regs = [_u(item, 32) for item in raw]
    regs[0] = 0
    return regs


def _resetting(inputs: dict, rst: bool) -> bool:
    return bool(rst) or _u(inputs.get("rst_n", 1), 1) == 0


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    regs = _regs_of(state)
    ra1 = _u(inputs.get("ra1", 0), 5)
    ra2 = _u(inputs.get("ra2", 0), 5)
    wa = _u(inputs.get("wa", 0), 5)
    wd = _u(inputs.get("wd", 0), 32)
    we = _u(inputs.get("we", 0), 1)
    if _resetting(inputs, rst):
        regs = [0] * 32
    elif we and wa != 0:
        regs[wa] = wd
    regs[0] = 0

    def read(addr: int) -> int:
        return 0 if addr == 0 else regs[addr]

    return {"regs": regs}, {"rd1": read(ra1), "rd2": read(ra2)}
