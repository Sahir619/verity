"""CPU_CONTRACT sections 2-3 and RV32I 2.1 Immediate Encoding Variants.

Source: https://docs.riscv.org/reference/isa/v20240411/unpriv/rv32.html
The bit maps below describe immediate bit -> instruction bit independently of RTL.
"""

FORMATS = {
    0x03: (12, tuple(range(20, 32))),
    0x13: (12, tuple(range(20, 32))),
    0x67: (12, tuple(range(20, 32))),
    0x23: (12, (*range(7, 12), *range(25, 32))),
    0x63: (13, (None, *range(8, 12), *range(25, 31), 7, 31)),
    0x37: (32, (None,) * 12 + tuple(range(12, 32))),
    0x17: (32, (None,) * 12 + tuple(range(12, 32))),
    0x6F: (21, (None, *range(21, 31), 20, *range(12, 20), 31)),
}


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    instr = inputs["instr"] & 0xFFFFFFFF
    width, mapping = FORMATS.get(instr & 0x7F, (1, (None,)))
    value = sum(((instr >> src) & 1) << dst for dst, src in enumerate(mapping) if src is not None)
    if value & (1 << (width - 1)):
        value -= 1 << width
    return {}, {"imm": value & 0xFFFFFFFF}
