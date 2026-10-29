"""RV32I 2.1 arithmetic, CPU_CONTRACT section 2 operation numbering.

Source: https://docs.riscv.org/reference/isa/v20240411/unpriv/rv32.html
"""


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    a, b = inputs["a"] & 0xFFFFFFFF, inputs["b"] & 0xFFFFFFFF
    sa = a if a < 0x80000000 else a - 0x100000000
    sb = b if b < 0x80000000 else b - 0x100000000
    shift = b % 32
    results = (a + b, a - b, a << shift, int(sa < sb), int(a < b),
               a ^ b, a >> shift, sa >> shift, a | b, a & b)
    op = inputs["op"] & 15
    return {}, {"y": (results[op] if op < 10 else 0) & 0xFFFFFFFF}
