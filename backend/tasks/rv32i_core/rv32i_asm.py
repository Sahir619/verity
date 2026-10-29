"""Small RV32I assembler. Standard library only.

Assembles the 37 base instructions, fence, ecall, ebreak, and the pseudos li, mv, nop, j, ret,
beqz and bnez. Supports labels, .org, .word and # comments. Registers are x0..x31 and the ABI names.
Output is a dict of byte address to a 32-bit instruction or data word. Nothing is written for a gap.
"""

from __future__ import annotations

import re

ABI = {
    "zero": 0,
    "ra": 1,
    "sp": 2,
    "gp": 3,
    "tp": 4,
    "t0": 5,
    "t1": 6,
    "t2": 7,
    "s0": 8,
    "fp": 8,
    "s1": 9,
    "a0": 10,
    "a1": 11,
    "a2": 12,
    "a3": 13,
    "a4": 14,
    "a5": 15,
    "a6": 16,
    "a7": 17,
    "s2": 18,
    "s3": 19,
    "s4": 20,
    "s5": 21,
    "s6": 22,
    "s7": 23,
    "s8": 24,
    "s9": 25,
    "s10": 26,
    "s11": 27,
    "t3": 28,
    "t4": 29,
    "t5": 30,
    "t6": 31,
}

# funct3 for branches, loads, stores, OP and OP-IMM.
_BRANCH = {"beq": 0, "bne": 1, "blt": 4, "bge": 5, "bltu": 6, "bgeu": 7}
_LOAD = {"lb": 0, "lh": 1, "lw": 2, "lbu": 4, "lhu": 5}
_STORE = {"sb": 0, "sh": 1, "sw": 2}
_OPIMM = {"addi": 0, "slti": 2, "sltiu": 3, "xori": 4, "ori": 6, "andi": 7}
_SHIFT_IMM = {"slli": (1, 0x00), "srli": (5, 0x00), "srai": (5, 0x20)}
_OP = {
    "add": (0, 0x00),
    "sub": (0, 0x20),
    "sll": (1, 0x00),
    "slt": (2, 0x00),
    "sltu": (3, 0x00),
    "xor": (4, 0x00),
    "srl": (5, 0x00),
    "sra": (5, 0x20),
    "or": (6, 0x00),
    "and": (7, 0x00),
}

_EXPECT_RE = re.compile(
    r"^\s*#\s*expect\s+(0x[0-9a-fA-F]+|\d+)\s*=\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*$"
)
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AssemblerError(ValueError):
    """One source line could not be assembled."""


def parse_expects(source: str) -> list[tuple[int, int]]:
    """Read `# expect <addr> = <word>` header lines. Values may be signed. Words are unsigned 32-bit."""
    found: list[tuple[int, int]] = []
    for line in source.splitlines():
        match = _EXPECT_RE.match(line)
        if not match:
            continue
        addr = _parse_int(match.group(1))
        value = _u32(_parse_int(match.group(2)))
        if addr < 0 or addr & 3:
            raise AssemblerError(f"expect address must be a word-aligned byte address, got {match.group(1)}")
        found.append((addr, value))
    return found


def assemble(source: str) -> dict[int, int]:
    """Assemble one program. Keys are byte addresses, values are little-endian words as integers."""
    items = _parse(source)
    labels: dict[str, int] = {}
    placed: list[tuple[int, _Item]] = []
    pc = 0
    for item in items:
        if item.kind == "label":
            if item.text in labels:
                raise AssemblerError(f"line {item.line}: label {item.text} is already defined")
            labels[item.text] = pc
        elif item.kind == "org":
            pc = item.number
        else:
            placed.append((pc, item))
            pc += _size(item)
    image: dict[int, int] = {}
    for addr, item in placed:
        for offset, word in enumerate(_encode(item, addr, labels)):
            at = addr + 4 * offset
            if at in image:
                raise AssemblerError(f"line {item.line}: word at 0x{at:x} is written twice")
            image[at] = word
    return image


class _Item:
    def __init__(self, kind: str, line: int, text: str = "", args: tuple[str, ...] = (), number: int = 0) -> None:
        self.kind = kind
        self.line = line
        self.text = text
        self.args = args
        self.number = number


def _parse(source: str) -> list[_Item]:
    items: list[_Item] = []
    for n, raw in enumerate(source.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        while text:
            if text.startswith("."):
                items.append(_parse_directive(text, n))
                break
            if ":" in text:
                name, _, rest = text.partition(":")
                name = name.strip()
                if not _LABEL_RE.match(name):
                    raise AssemblerError(f"line {n}: bad label {name!r}")
                items.append(_Item("label", n, text=name))
                text = rest.strip()
                continue
            parts = text.replace(",", " ").split()
            items.append(_Item("insn", n, text=parts[0].lower(), args=tuple(parts[1:])))
            break
    return items


def _parse_directive(text: str, line: int) -> _Item:
    parts = text.replace(",", " ").split()
    name = parts[0].lower()
    args = parts[1:]
    if name == ".org":
        if len(args) != 1:
            raise AssemblerError(f"line {line}: .org needs one address")
        addr = _parse_int(args[0])
        if addr < 0 or addr & 3:
            raise AssemblerError(f"line {line}: .org address must be word aligned and non-negative")
        return _Item("org", line, number=addr)
    if name == ".word":
        if len(args) != 1:
            raise AssemblerError(f"line {line}: .word needs one value (one per line)")
        return _Item("word", line, args=tuple(args))
    raise AssemblerError(f"line {line}: unknown directive {name}")


def _size(item: _Item) -> int:
    if item.kind == "word":
        return 4
    if item.text == "li":
        return 4 * len(_li_parts(_require_reg_imm(item)))
    return 4


def _encode(item: _Item, pc: int, labels: dict[str, int]) -> list[int]:
    if item.kind == "word":
        return [_resolve(item.args[0], labels, pc, relative=False, line=item.line) & 0xFFFFFFFF]
    op = item.text
    args = item.args
    try:
        if op == "lui" or op == "auipc":
            rd, imm = _reg_uimm(args, item.line)
            return [_u(imm, rd, 0x37 if op == "lui" else 0x17)]
        if op == "jal":
            rd, target = _reg_target(args, item.line)
            return [_j(_rel(target, labels, pc, item.line), rd)]
        if op == "jalr":
            rd, rs1, imm = _jalr_args(args, labels, pc, item.line)
            return [_i(imm, rs1, 0, rd, 0x67)]
        if op in _BRANCH:
            rs1, rs2, target = _branch_args(args, item.line)
            return [_b(_rel(target, labels, pc, item.line), rs2, rs1, _BRANCH[op])]
        if op in _LOAD:
            rd, imm, rs1 = _load_args(args, labels, pc, item.line)
            return [_i(imm, rs1, _LOAD[op], rd, 0x03)]
        if op in _STORE:
            rs2, imm, rs1 = _store_args(args, labels, pc, item.line)
            return [_s(imm, rs2, rs1, _STORE[op])]
        if op in _OPIMM:
            rd, rs1, imm = _rr_imm(args, item.line)
            return [_i(imm, rs1, _OPIMM[op], rd, 0x13)]
        if op in _SHIFT_IMM:
            rd, rs1, shamt = _rr_shamt(args, item.line)
            funct3, funct7 = _SHIFT_IMM[op]
            return [_i((funct7 << 5) | shamt, rs1, funct3, rd, 0x13)]
        if op in _OP:
            rd, rs1, rs2 = _rrr(args, item.line)
            funct3, funct7 = _OP[op]
            return [_r(funct7, rs2, rs1, funct3, rd, 0x33)]
        if op == "fence":
            if args:
                raise AssemblerError(f"line {item.line}: fence takes no operands")
            return [0x0FF0000F]
        if op == "ecall":
            if args:
                raise AssemblerError(f"line {item.line}: ecall takes no operands")
            return [0x00000073]
        if op == "ebreak":
            if args:
                raise AssemblerError(f"line {item.line}: ebreak takes no operands")
            return [0x00100073]
        if op == "li":
            rd, imm = _require_reg_imm(item)
            return _li_words(rd, imm)
        if op == "mv":
            if len(args) != 2:
                raise AssemblerError(f"line {item.line}: mv rd, rs")
            return [_i(0, _reg(args[1], item.line), 0, _reg(args[0], item.line), 0x13)]
        if op == "nop":
            if args:
                raise AssemblerError(f"line {item.line}: nop takes no operands")
            return [0x00000013]
        if op == "j":
            if len(args) != 1:
                raise AssemblerError(f"line {item.line}: j target")
            return [_j(_rel(args[0], labels, pc, item.line), 0)]
        if op == "ret":
            if args:
                raise AssemblerError(f"line {item.line}: ret takes no operands")
            return [_i(0, 1, 0, 0, 0x67)]
        if op == "beqz" or op == "bnez":
            if len(args) != 2:
                raise AssemblerError(f"line {item.line}: {op} rs, target")
            funct3 = 0 if op == "beqz" else 1
            return [_b(_rel(args[1], labels, pc, item.line), 0, _reg(args[0], item.line), funct3)]
    except AssemblerError:
        raise
    except ValueError as exc:
        raise AssemblerError(f"line {item.line}: {exc}") from exc
    raise AssemblerError(f"line {item.line}: unknown instruction {op}")


def _li_parts(pair: tuple[int, int]) -> list[tuple[str, int]]:
    """Return the (kind, value) pieces of li. kind is 'addi0' or 'lui' plus an optional 'addi'."""
    _rd, imm = pair
    imm &= 0xFFFFFFFF
    signed = imm - 0x100000000 if imm & 0x80000000 else imm
    if -2048 <= signed <= 2047:
        return [("addi0", signed)]
    hi = (imm + 0x800) & 0xFFFFFFFF
    parts = [("lui", (hi >> 12) & 0xFFFFF)]
    lo = imm & 0xFFF
    if lo:
        parts.append(("addi", lo - 0x1000 if lo & 0x800 else lo))
    return parts


def _require_reg_imm(item: _Item) -> tuple[int, int]:
    if len(item.args) != 2:
        raise AssemblerError(f"line {item.line}: li rd, imm")
    return _reg(item.args[0], item.line), _parse_int(item.args[1])


def _li_words(rd: int, imm: int) -> list[int]:
    words: list[int] = []
    for kind, value in _li_parts((rd, imm)):
        if kind == "addi0":
            words.append(_i(value, 0, 0, rd, 0x13))
        elif kind == "lui":
            words.append(_u(value, rd, 0x37))
        else:
            words.append(_i(value, rd, 0, rd, 0x13))
    return words


def _r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    return ((funct7 & 0x7F) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode


def _i(imm: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    return ((_imm12(imm) & 0xFFF) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode


def _s(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    imm12 = _imm12(imm)
    return (
        ((imm12 >> 5) & 0x7F) << 25
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | ((imm12 & 0x1F) << 7)
        | 0x23
    )


def _b(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    if imm & 1 or imm < -4096 or imm > 4094:
        raise ValueError(f"branch offset {imm} is out of range or odd")
    raw = imm & 0x1FFF
    return (
        ((raw >> 12) & 1) << 31
        | ((raw >> 5) & 0x3F) << 25
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | ((raw >> 1) & 0xF) << 8
        | ((raw >> 11) & 1) << 7
        | 0x63
    )


def _u(imm20: int, rd: int, opcode: int) -> int:
    return ((imm20 & 0xFFFFF) << 12) | ((rd & 31) << 7) | opcode


def _j(imm: int, rd: int) -> int:
    if imm & 1 or imm < -(1 << 20) or imm >= (1 << 20):
        raise ValueError(f"jal offset {imm} is out of range or odd")
    raw = imm & 0x1FFFFF
    return (
        ((raw >> 20) & 1) << 31
        | ((raw >> 1) & 0x3FF) << 21
        | ((raw >> 11) & 1) << 20
        | ((raw >> 12) & 0xFF) << 12
        | ((rd & 31) << 7)
        | 0x6F
    )


def _imm12(imm: int) -> int:
    if imm < -2048 or imm > 2047:
        raise ValueError(f"immediate {imm} does not fit in 12 bits")
    return imm & 0xFFF


def _reg(tok: str, line: int) -> int:
    if tok in ABI:
        return ABI[tok]
    if len(tok) >= 2 and tok[0] == "x" and tok[1:].isdigit():
        n = int(tok[1:])
        if 0 <= n <= 31:
            return n
    raise AssemblerError(f"line {line}: bad register {tok!r}")


def _parse_int(tok: str) -> int:
    sign = 1
    body = tok
    if body.startswith("-"):
        sign = -1
        body = body[1:]
    if body.lower().startswith("0x"):
        if len(body) == 2:
            raise ValueError(f"bad number {tok!r}")
        return sign * int(body, 16)
    if not body.isdigit():
        raise ValueError(f"bad number {tok!r}")
    return sign * int(body, 10)


def _u32(value: int) -> int:
    if value < 0:
        value += 1 << 32
    return value & 0xFFFFFFFF


def _resolve(tok: str, labels: dict[str, int], pc: int, *, relative: bool, line: int) -> int:
    if _LABEL_RE.match(tok) and not (len(tok) >= 2 and tok[0] == "x" and tok[1:].isdigit()) and tok not in ABI:
        if tok not in labels:
            raise AssemblerError(f"line {line}: unknown label {tok}")
        return (labels[tok] - pc) if relative else labels[tok]
    try:
        return _parse_int(tok)
    except ValueError as exc:
        raise AssemblerError(f"line {line}: {exc}") from exc


def _rel(tok: str, labels: dict[str, int], pc: int, line: int) -> int:
    return _resolve(tok, labels, pc, relative=True, line=line)


def _reg_uimm(args: tuple[str, ...], line: int) -> tuple[int, int]:
    if len(args) != 2:
        raise AssemblerError(f"line {line}: lui/auipc rd, imm20")
    imm = _parse_int(args[1])
    if imm < -0x80000 or imm > 0xFFFFF:
        raise AssemblerError(f"line {line}: U immediate {imm} does not fit in 20 bits")
    return _reg(args[0], line), imm & 0xFFFFF


def _reg_target(args: tuple[str, ...], line: int) -> tuple[int, str]:
    if len(args) != 2:
        raise AssemblerError(f"line {line}: jal rd, target")
    return _reg(args[0], line), args[1]


def _jalr_args(args: tuple[str, ...], labels: dict[str, int], pc: int, line: int) -> tuple[int, int, int]:
    if len(args) == 2 and "(" not in args[1]:
        return _reg(args[0], line), _reg(args[1], line), 0
    if len(args) == 2:
        rd = _reg(args[0], line)
        imm, rs1 = _memop(args[1], labels, pc, line)
        return rd, rs1, imm
    if len(args) == 3:
        imm = _resolve(args[2], labels, pc, relative=False, line=line)
        return _reg(args[0], line), _reg(args[1], line), imm
    raise AssemblerError(f"line {line}: jalr rd, rs1, imm or jalr rd, imm(rs1)")


def _branch_args(args: tuple[str, ...], line: int) -> tuple[int, int, str]:
    if len(args) != 3:
        raise AssemblerError(f"line {line}: branch rs1, rs2, target")
    return _reg(args[0], line), _reg(args[1], line), args[2]


def _load_args(args: tuple[str, ...], labels: dict[str, int], pc: int, line: int) -> tuple[int, int, int]:
    if len(args) != 2:
        raise AssemblerError(f"line {line}: load rd, offset(rs1)")
    imm, rs1 = _memop(args[1], labels, pc, line)
    return _reg(args[0], line), imm, rs1


def _store_args(args: tuple[str, ...], labels: dict[str, int], pc: int, line: int) -> tuple[int, int, int]:
    if len(args) != 2:
        raise AssemblerError(f"line {line}: store rs2, offset(rs1)")
    imm, rs1 = _memop(args[1], labels, pc, line)
    return _reg(args[0], line), imm, rs1


def _rr_imm(args: tuple[str, ...], line: int) -> tuple[int, int, int]:
    if len(args) != 3:
        raise AssemblerError(f"line {line}: op rd, rs1, imm")
    return _reg(args[0], line), _reg(args[1], line), _parse_int(args[2])


def _rr_shamt(args: tuple[str, ...], line: int) -> tuple[int, int, int]:
    rd, rs1, shamt = _rr_imm(args, line)
    if shamt < 0 or shamt > 31:
        raise AssemblerError(f"line {line}: shift amount {shamt} is out of range")
    return rd, rs1, shamt


def _rrr(args: tuple[str, ...], line: int) -> tuple[int, int, int]:
    if len(args) != 3:
        raise AssemblerError(f"line {line}: op rd, rs1, rs2")
    return _reg(args[0], line), _reg(args[1], line), _reg(args[2], line)


def _memop(tok: str, labels: dict[str, int], pc: int, line: int) -> tuple[int, int]:
    if not (tok.endswith(")") and "(" in tok):
        raise AssemblerError(f"line {line}: expected offset(reg), got {tok!r}")
    imm_s, reg_s = tok[:-1].split("(", 1)
    rs1 = _reg(reg_s, line)
    if imm_s == "":
        return 0, rs1
    return _resolve(imm_s, labels, pc, relative=False, line=line), rs1
