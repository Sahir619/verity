"""Independent RV32I interpreter for the rv32i_core pack.

Written from the RISC-V unprivileged spec and CPU_CONTRACT.md section 4, not from any Verilog.
FENCE retires as a no-op. ECALL, EBREAK, illegal encodings, misaligned loads or stores, and a taken
branch, JAL or JALR whose target is not a multiple of 4 (no C extension) stop the machine without
retiring. Writes to x0 are dropped. Registers and the PC reset to 0.
Memory is 64 KiB, little endian. Word accesses use address bits [15:2].

`execute` returns the retire list (pc, insn, rd, wdata) and the final memory. A retire that
writes nothing uses rd 0 and wdata 0. `disassemble` is for failure messages.
"""

from __future__ import annotations

from dataclasses import dataclass

MEM_BYTES = 64 * 1024
_XNAMES = tuple(f"x{n}" for n in range(32))


@dataclass(frozen=True)
class Retire:
    pc: int
    insn: int
    rd: int
    wdata: int


def execute(image: dict[int, int], *, max_steps: int = 10_000) -> tuple[list[Retire], bytearray]:
    """Run until a trap or max_steps. The trapping instruction is not in the retire list."""
    mem = bytearray(MEM_BYTES)
    for addr, word in image.items():
        if addr < 0 or addr & 3:
            raise ValueError(f"image address 0x{addr:x} is not a word address")
        _put_word(mem, addr, word & 0xFFFFFFFF)
    regs = [0] * 32
    pc = 0
    retires: list[Retire] = []
    for _ in range(max_steps):
        insn = read_word(mem, pc)
        stepped = _step(regs, mem, pc, insn)
        if stepped is None:
            break
        next_pc, rd, wdata = stepped
        retires.append(Retire(pc & 0xFFFFFFFF, insn & 0xFFFFFFFF, rd & 31, wdata & 0xFFFFFFFF))
        pc = next_pc & 0xFFFFFFFF
        regs[0] = 0
    return retires, mem


def read_word(mem: bytearray, addr: int) -> int:
    """Little-endian word at address bits [15:2]. Low bits of addr are ignored."""
    base = ((addr >> 2) & 0x3FFF) << 2
    return mem[base] | (mem[base + 1] << 8) | (mem[base + 2] << 16) | (mem[base + 3] << 24)


def disassemble(insn: int) -> str:
    """One instruction in a short assembly form, or `illegal` when the encoding traps."""
    insn &= 0xFFFFFFFF
    name = _classify(insn)
    if name is None:
        return "illegal"
    rd = (insn >> 7) & 31
    f3 = (insn >> 12) & 7
    rs1 = (insn >> 15) & 31
    rs2 = (insn >> 20) & 31
    if name in ("lui", "auipc"):
        return f"{name} {_XNAMES[rd]}, 0x{(insn >> 12) & 0xFFFFF:x}"
    if name == "jal":
        return f"jal {_XNAMES[rd]}, {_j_imm(insn)}"
    if name == "jalr":
        return f"jalr {_XNAMES[rd]}, {_i_imm(insn)}({_XNAMES[rs1]})"
    if name in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
        return f"{name} {_XNAMES[rs1]}, {_XNAMES[rs2]}, {_b_imm(insn)}"
    if name in ("lb", "lh", "lw", "lbu", "lhu"):
        return f"{name} {_XNAMES[rd]}, {_i_imm(insn)}({_XNAMES[rs1]})"
    if name in ("sb", "sh", "sw"):
        return f"{name} {_XNAMES[rs2]}, {_s_imm(insn)}({_XNAMES[rs1]})"
    if name in ("slli", "srli", "srai"):
        return f"{name} {_XNAMES[rd]}, {_XNAMES[rs1]}, {(insn >> 20) & 31}"
    if name in ("addi", "slti", "sltiu", "xori", "ori", "andi"):
        return f"{name} {_XNAMES[rd]}, {_XNAMES[rs1]}, {_i_imm(insn)}"
    if name in ("add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"):
        return f"{name} {_XNAMES[rd]}, {_XNAMES[rs1]}, {_XNAMES[rs2]}"
    if name == "fence":
        return "fence"
    if name == "ecall":
        return "ecall"
    if name == "ebreak":
        return "ebreak"
    return "illegal"


def _classify(insn: int) -> str | None:
    """Mnemonic, or None when this encoding traps (illegal, ecall or ebreak)."""
    op = insn & 0x7F
    f3 = (insn >> 12) & 7
    f7 = (insn >> 25) & 0x7F
    if op == 0x37:
        return "lui"
    if op == 0x17:
        return "auipc"
    if op == 0x6F:
        return "jal"
    if op == 0x67:
        return "jalr" if f3 == 0 else None
    if op == 0x63:
        return {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}.get(f3)
    if op == 0x03:
        return {0: "lb", 1: "lh", 2: "lw", 4: "lbu", 5: "lhu"}.get(f3)
    if op == 0x23:
        return {0: "sb", 1: "sh", 2: "sw"}.get(f3)
    if op == 0x13:
        if f3 == 0:
            return "addi"
        if f3 == 2:
            return "slti"
        if f3 == 3:
            return "sltiu"
        if f3 == 4:
            return "xori"
        if f3 == 6:
            return "ori"
        if f3 == 7:
            return "andi"
        if f3 == 1:
            return "slli" if f7 == 0x00 else None
        if f3 == 5:
            if f7 == 0x00:
                return "srli"
            if f7 == 0x20:
                return "srai"
        return None
    if op == 0x33:
        if f7 == 0x20 and f3 == 0:
            return "sub"
        if f7 == 0x20 and f3 == 5:
            return "sra"
        if f7 != 0x00:
            return None
        return {0: "add", 1: "sll", 2: "slt", 3: "sltu", 4: "xor", 5: "srl", 6: "or", 7: "and"}.get(f3)
    if op == 0x0F:
        return "fence"
    if insn == 0x00000073:
        return "ecall"
    if insn == 0x00100073:
        return "ebreak"
    return None


def _step(regs: list[int], mem: bytearray, pc: int, insn: int) -> tuple[int, int, int] | None:
    name = _classify(insn)
    if name is None or name in ("ecall", "ebreak"):
        return None
    rd = (insn >> 7) & 31
    rs1 = (insn >> 15) & 31
    rs2 = (insn >> 20) & 31
    a = regs[rs1]
    b = regs[rs2]
    if name == "lui":
        return _retire(regs, pc + 4, rd, _u_imm(insn))
    if name == "auipc":
        return _retire(regs, pc + 4, rd, (pc + _u_imm(insn)) & 0xFFFFFFFF)
    if name == "jal":
        target = (pc + _j_imm(insn)) & 0xFFFFFFFF
        if target & 3:
            return None
        return _retire(regs, target, rd, (pc + 4) & 0xFFFFFFFF)
    if name == "jalr":
        target = (a + _i_imm(insn)) & 0xFFFFFFFE
        if target & 3:
            return None
        return _retire(regs, target, rd, (pc + 4) & 0xFFFFFFFF)
    if name in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
        taken = _taken(name, a, b)
        nxt = (pc + _b_imm(insn)) if taken else (pc + 4)
        if nxt & 3:
            return None
        return _retire(regs, nxt & 0xFFFFFFFF, 0, 0, write=False)
    if name in ("lb", "lh", "lw", "lbu", "lhu"):
        addr = (a + _i_imm(insn)) & 0xFFFFFFFF
        if _misaligned(addr, name):
            return None
        return _retire(regs, pc + 4, rd, _load(mem, addr, name))
    if name in ("sb", "sh", "sw"):
        addr = (a + _s_imm(insn)) & 0xFFFFFFFF
        if _misaligned(addr, name):
            return None
        _store(mem, addr, name, b)
        return _retire(regs, pc + 4, 0, 0, write=False)
    if name == "fence":
        return _retire(regs, pc + 4, 0, 0, write=False)
    alu_b = b if name in ("add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and") else (_i_imm(insn) & 0xFFFFFFFF)
    return _retire(regs, pc + 4, rd, _alu(name, a, alu_b))


def _retire(regs: list[int], next_pc: int, rd: int, value: int, *, write: bool = True) -> tuple[int, int, int]:
    value &= 0xFFFFFFFF
    next_pc &= 0xFFFFFFFF
    if write and rd != 0:
        regs[rd] = value
        return next_pc, rd, value
    return next_pc, 0, 0


def _taken(name: str, a: int, b: int) -> bool:
    if name == "beq":
        return a == b
    if name == "bne":
        return a != b
    if name == "blt":
        return _signed(a) < _signed(b)
    if name == "bge":
        return _signed(a) >= _signed(b)
    if name == "bltu":
        return a < b
    return a >= b


def _misaligned(addr: int, name: str) -> bool:
    if name in ("lb", "lbu", "sb"):
        return False
    if name in ("lh", "lhu", "sh"):
        return bool(addr & 1)
    return bool(addr & 3)


def _load(mem: bytearray, addr: int, name: str) -> int:
    b = addr & 0xFFFF
    if name in ("lb", "lbu"):
        val = mem[b]
        if name == "lb" and val & 0x80:
            val |= 0xFFFFFF00
        return val
    if name in ("lh", "lhu"):
        val = mem[b] | (mem[b + 1] << 8)
        if name == "lh" and val & 0x8000:
            val |= 0xFFFF0000
        return val
    return read_word(mem, addr)


def _store(mem: bytearray, addr: int, name: str, value: int) -> None:
    b = addr & 0xFFFF
    if name == "sb":
        mem[b] = value & 0xFF
        return
    if name == "sh":
        mem[b] = value & 0xFF
        mem[b + 1] = (value >> 8) & 0xFF
        return
    _put_word(mem, addr, value)


def _alu(name: str, a: int, b: int) -> int:
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    sh = b & 31
    if name in ("add", "addi"):
        return (a + b) & 0xFFFFFFFF
    if name == "sub":
        return (a - b) & 0xFFFFFFFF
    if name in ("sll", "slli"):
        return (a << sh) & 0xFFFFFFFF
    if name in ("slt", "slti"):
        return 1 if _signed(a) < _signed(b) else 0
    if name in ("sltu", "sltiu"):
        return 1 if a < b else 0
    if name in ("xor", "xori"):
        return a ^ b
    if name in ("srl", "srli"):
        return a >> sh
    if name in ("sra", "srai"):
        return (_signed(a) >> sh) & 0xFFFFFFFF
    if name in ("or", "ori"):
        return a | b
    if name in ("and", "andi"):
        return a & b
    return 0


def _put_word(mem: bytearray, addr: int, word: int) -> None:
    base = ((addr >> 2) & 0x3FFF) << 2
    mem[base] = word & 0xFF
    mem[base + 1] = (word >> 8) & 0xFF
    mem[base + 2] = (word >> 16) & 0xFF
    mem[base + 3] = (word >> 24) & 0xFF


def _signed(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _sx(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def _i_imm(insn: int) -> int:
    return _sx(insn >> 20, 12)


def _s_imm(insn: int) -> int:
    return _sx(((insn >> 25) << 5) | ((insn >> 7) & 0x1F), 12)


def _b_imm(insn: int) -> int:
    raw = ((insn >> 31) & 1) << 12 | ((insn >> 7) & 1) << 11 | ((insn >> 25) & 0x3F) << 5 | ((insn >> 8) & 0xF) << 1
    return _sx(raw, 13)


def _u_imm(insn: int) -> int:
    return insn & 0xFFFFF000


def _j_imm(insn: int) -> int:
    raw = ((insn >> 31) & 1) << 20 | ((insn >> 12) & 0xFF) << 12 | ((insn >> 20) & 1) << 11 | ((insn >> 21) & 0x3FF) << 1
    return _sx(raw, 21)
