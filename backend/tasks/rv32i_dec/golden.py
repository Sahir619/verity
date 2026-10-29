"""Table-driven controls from CPU_CONTRACT section 2, independently of RTL.

ISA source: https://docs.riscv.org/reference/isa/v20240411/unpriv/rv32.html
The contract makes every opcode 0x0f a no-op, including reserved FENCE fields.
"""

OUTPUTS = ("alu_op", "a_sel", "b_sel", "wb_sel", "rf_we", "is_load",
           "is_store", "is_branch", "is_jal", "is_jalr", "trap")
ALU_BY_FUNCT3 = (0, 2, 3, 4, 5, 6, 8, 9)
# opcode: (legal funct3, a_sel, wb_sel, rf_we, flag)
GROUPS = {
    0x37: (range(8), 2, 0, 1, None),
    0x17: (range(8), 1, 0, 1, None),
    0x6F: (range(8), 1, 2, 1, "is_jal"),
    0x67: ((0,), 0, 2, 1, "is_jalr"),
    0x63: ((0, 1, 4, 5, 6, 7), 1, 0, 0, "is_branch"),
    0x03: ((0, 1, 2, 4, 5), 0, 1, 1, "is_load"),
    0x23: ((0, 1, 2), 0, 0, 0, "is_store"),
}


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    instr = inputs["instr"] & 0xFFFFFFFF
    opcode, f3, f7 = instr & 127, (instr >> 12) & 7, instr >> 25
    out = dict.fromkeys(OUTPUTS, 0)
    out["trap"] = 1
    if opcode == 0x0F:
        out["trap"] = 0
    elif opcode in GROUPS:
        legal, a_sel, wb_sel, rf_we, flag = GROUPS[opcode]
        if f3 in legal:
            out.update(trap=0, a_sel=a_sel, b_sel=1, wb_sel=wb_sel, rf_we=rf_we)
            if flag:
                out[flag] = 1
    elif opcode in (0x13, 0x33):
        legal = f7 == 0 or (f7 == 0x20 and f3 in (0, 5))
        if opcode == 0x13:
            legal = f3 not in (1, 5) or f7 == 0 or (f3 == 5 and f7 == 0x20)
        if legal:
            op = ALU_BY_FUNCT3[f3]
            if f7 == 0x20 and (f3 == 5 or (opcode == 0x33 and f3 == 0)):
                op = 7 if f3 == 5 else 1
            out.update(trap=0, alu_op=op, b_sel=int(opcode == 0x13), rf_we=1)
    return {}, out
