"""Golden model for rv32i_lsu. Written from the CPU contract, not from reference.v.

Combinational. One step is one input vector. size is funct3[1:0]: 0 byte, 1 half, 2 word,
3 invalid. An invalid size fixes sdata at 0 (the contract states the lane pattern for byte,
half and word only).
"""

from __future__ import annotations


def _u(value: object, bits: int) -> int:
    return int(value) & ((1 << bits) - 1)


def _sext(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    if value >> (bits - 1):
        value |= (-1 << bits) & 0xFFFFFFFF
    return value & 0xFFFFFFFF


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    del state, rst  # combinational: no state, reset is not a port
    addr_lo = _u(inputs.get("addr_lo", 0), 2)
    funct3 = _u(inputs.get("funct3", 0), 3)
    rdata = _u(inputs.get("rdata", 0), 32)
    wdata = _u(inputs.get("wdata", 0), 32)
    size = funct3 & 3
    unsigned = (funct3 >> 2) & 1
    misaligned = int((size == 1 and (addr_lo & 1) == 1) or (size == 2 and addr_lo != 0) or size == 3)
    shifted = (rdata >> (8 * addr_lo)) & 0xFFFFFFFF
    illegal_ld = funct3 in (0b011, 0b110, 0b111)
    if misaligned or illegal_ld:
        ld = 0
    elif size == 0:
        ld = (shifted & 0xFF) if unsigned else _sext(shifted & 0xFF, 8)
    elif size == 1:
        ld = (shifted & 0xFFFF) if unsigned else _sext(shifted & 0xFFFF, 16)
    else:
        ld = rdata
    if misaligned or unsigned:
        wstrb = 0
    elif size == 0:
        wstrb = (0b0001 << addr_lo) & 0xF
    elif size == 1:
        wstrb = (0b0011 << addr_lo) & 0xF
    elif size == 2:
        wstrb = 0b1111
    else:
        wstrb = 0
    if size == 0:
        byte = wdata & 0xFF
        sdata = byte | (byte << 8) | (byte << 16) | (byte << 24)
    elif size == 1:
        half = wdata & 0xFFFF
        sdata = half | (half << 16)
    elif size == 2:
        sdata = wdata
    else:
        sdata = 0
    return {}, {"ld": ld & 0xFFFFFFFF, "wstrb": wstrb & 0xF, "sdata": sdata & 0xFFFFFFFF, "misaligned": misaligned}
