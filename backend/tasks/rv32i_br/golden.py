"""RV32I 2.1 conditional branches, CPU_CONTRACT sections 2-3.

Source: https://docs.riscv.org/reference/isa/v20240411/unpriv/rv32.html
"""


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    a, b = inputs["a"] & 0xFFFFFFFF, inputs["b"] & 0xFFFFFFFF
    # Biasing the sign bit maps two's-complement order into unsigned order.
    signed_less = (a ^ 0x80000000) < (b ^ 0x80000000)
    predicates = (a == b, a != b, False, False, signed_less,
                  not signed_less, a < b, a >= b)
    return {}, {"taken": int(predicates[inputs["funct3"] & 7])}
