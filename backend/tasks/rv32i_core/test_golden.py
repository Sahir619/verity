"""Hand checks for the assembler and the RV32I golden model.

Encodings below were calculated from the bit fields, not by calling the assembler.
Program expectations were calculated by hand in each program's header comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import golden
import rv32i_asm
from golden import Retire, disassemble, execute, read_word
from rv32i_asm import assemble, parse_expects

EBREAK = 0x00100073
PROGRAMS = ROOT / "programs"


def _word(mem: bytearray, addr: int) -> int:
    return read_word(mem, addr)


def test_hand_encodings_match_the_assembler() -> None:
    # addi x0, x0, 0 is nop: imm 0, rs1 0, funct3 0, rd 0, opcode 0x13.
    assert assemble("nop\n")[0] == 0x00000013
    # addi x10, x0, 55: imm 0x037 << 20 | rd 10 << 7 | opcode 0x13 = 0x03700513.
    assert assemble("addi a0, x0, 55\n")[0] == 0x03700513
    # addi x0, x0, 5: imm 5 << 20 | opcode 0x13 = 0x00500013.
    assert assemble("addi x0, x0, 5\n")[0] == 0x00500013
    # lui x1, 0x12345 = 0x12345000 | (1 << 7) | 0x37 = 0x123450B7.
    assert assemble("lui x1, 0x12345\n")[0] == 0x123450B7
    # beq x1, x2, 8: imm[4:1]=4, rs2=2, rs1=1, opcode 0x63 -> 0x00208463.
    assert assemble("beq x1, x2, 8\n")[0] == 0x00208463
    # sw x2, -4(x1): imm 0xFFC, rs2 2, rs1 1, funct3 2 -> 0xFE20AE23.
    assert assemble("sw x2, -4(x1)\n")[0] == 0xFE20AE23
    # jal x1, 8: imm[10:1]=4, rd 1, opcode 0x6f -> 0x008000EF.
    assert assemble("jal x1, 8\n")[0] == 0x008000EF
    assert assemble("ecall\n")[0] == 0x00000073
    assert assemble("ebreak\n")[0] == EBREAK
    # fence iorw, iorw: pred=succ=0xF, opcode 0x0f -> 0x0ff0000f.
    assert assemble("fence\n")[0] == 0x0FF0000F
    # srai x2, x1, 31: imm 0x41F, rs1 1, funct3 5, rd 2 -> 0x41F0D113.
    assert assemble("srai x2, x1, 31\n")[0] == 0x41F0D113


def test_li_uses_the_0x800_rounding() -> None:
    # 0x800 does not fit in a signed 12-bit immediate. (0x800 + 0x800) >> 12 = 1, low half is 0x800.
    # LUI x1, 1 then ADDI x1, x1, -2048.
    words = assemble("li x1, 0x800\n")
    assert words[0] == assemble("lui x1, 1\n")[0]
    assert words[4] == assemble("addi x1, x1, -2048\n")[0]
    # A value that fits in 12 bits is a single addi from x0.
    assert assemble("li x1, -2048\n") == {0: assemble("addi x1, x0, -2048\n")[0]}
    # 0x80000000 is LUI only: (0x80000000 + 0x800) >> 12 masked to 20 bits is 0x80000, low 12 bits are 0.
    assert assemble("li x1, 0x80000000\n") == {0: assemble("lui x1, 0x80000\n")[0]}
    # mv, j, ret, beqz.
    assert assemble("mv x2, x3\n")[0] == assemble("addi x2, x3, 0\n")[0]
    assert assemble("ret\n")[0] == assemble("jalr x0, ra, 0\n")[0]
    assert assemble("beqz x1, 8\n")[0] == assemble("beq x1, x0, 8\n")[0]


def test_labels_and_word() -> None:
    image = assemble("j skip\nnop\nskip:\nebreak\n.org 0x1000\n.word 55\n.word skip\n")
    assert image[0] == assemble("jal x0, 8\n")[0]
    assert image[4] == 0x00000013
    assert image[8] == EBREAK
    assert image[0x1000] == 55
    assert image[0x1004] == 8


def test_addi_and_add_wrap_by_hand() -> None:
    # addi x1, x0, 5 = 0x00500093. addi x2, x0, 1 = 0x00100113.
    # add x3, x1, x2: rs2=2 rs1=1 rd=3 opcode 0x33 -> 0x002081B3. Result 6.
    retires, _mem = execute({0: 0x00500093, 4: 0x00100113, 8: 0x002081B3, 12: EBREAK})
    assert retires == [
        Retire(0, 0x00500093, 1, 5),
        Retire(4, 0x00100113, 2, 1),
        Retire(8, 0x002081B3, 3, 6),
    ]
    # addi x1, x0, -1 = 0xfff00093. addi x2, x1, 1 wraps to 0.
    retires, _mem = execute({0: 0xFFF00093, 4: 0x00108113, 8: EBREAK})
    assert retires[1] == Retire(4, 0x00108113, 2, 0)


def test_srai_of_the_sign_bit_by_hand() -> None:
    # lui x1, 0x80000 = 0x800000B7. srai x2, x1, 31 = 0x41F0D113. Result is all ones.
    retires, _mem = execute({0: 0x800000B7, 4: 0x41F0D113, 8: EBREAK})
    assert retires[1] == Retire(4, 0x41F0D113, 2, 0xFFFFFFFF)


def test_x0_write_is_dropped_by_hand() -> None:
    # addi x0, x0, 5 = 0x00500013. addi x1, x0, 7 must still see x0 as 0.
    addi_x0 = 0x00500013
    addi_x1 = 0x00700093
    retires, _mem = execute({0: addi_x0, 4: addi_x1, 8: EBREAK})
    assert retires[0] == Retire(0, addi_x0, 0, 0)
    assert retires[1] == Retire(4, addi_x1, 1, 7)


def test_store_byte_sign_extends_on_lb_by_hand() -> None:
    # addi x2, x0, -2 = 0xFFE00113 (low byte 0xFE). sb x2, 0(x0) = 0x00200023.
    # lb x3, 0(x0) = 0x00000183. The loaded value is 0xFFFFFFFE and the word in memory is 0xFE.
    retires, mem = execute({0: 0xFFE00113, 4: 0x00200023, 8: 0x00000183, 12: EBREAK})
    assert retires[2] == Retire(8, 0x00000183, 3, 0xFFFFFFFE)
    # The store hits byte 0 of the instruction word, so only that byte is 0xFE.
    assert mem[0] == 0xFE


def test_misaligned_load_does_not_retire_by_hand() -> None:
    # addi x1, x0, 1 = 0x00100093. lw x2, 0(x1) = 0x0000A103. The lw is misaligned and does not retire.
    retires, _mem = execute({0: 0x00100093, 4: 0x0000A103, 8: EBREAK})
    assert retires == [Retire(0, 0x00100093, 1, 1)]


def test_jalr_clears_bit_0_by_hand() -> None:
    # addi x1, x0, 21 = 0x01500093. jalr x0, x1, 0 = 0x00008067. Target is 20, not 21.
    # Address 20 holds addi x2, x0, 7 = 0x00700113, then ebreak.
    image = {0: 0x01500093, 4: 0x00008067, 20: 0x00700113, 24: EBREAK}
    retires, _mem = execute(image)
    assert [r.pc for r in retires] == [0, 4, 20]
    assert retires[2] == Retire(20, 0x00700113, 2, 7)


def test_fence_retires_and_ecall_does_not() -> None:
    retires, _mem = execute({0: 0x0FF0000F, 4: EBREAK})
    assert retires == [Retire(0, 0x0FF0000F, 0, 0)]
    assert disassemble(0x0FF0000F) == "fence"
    retires, _mem = execute({0: 0x00000073})
    assert retires == []
    assert disassemble(0x00000073) == "ecall"
    assert disassemble(0) == "illegal"


def test_each_program_matches_its_header() -> None:
    paths = sorted(PROGRAMS.glob("*.s"))
    assert len(paths) >= 10
    for path in paths:
        source = path.read_text(encoding="utf-8")
        expects = parse_expects(source)
        assert expects, path.name
        image = assemble(source)
        retires, mem = execute(image)
        assert retires, path.name
        assert all(word != EBREAK for _pc, word in ((r.pc, r.insn) for r in retires))
        for addr, value in expects:
            got = _word(mem, addr)
            assert got == value, f"{path.name}: 0x{addr:x} expected 0x{value:x} got 0x{got:x}"


def test_trap_programs_stop_before_the_later_store() -> None:
    for name in ("13_misaligned.s", "14_ecall.s", "15_illegal.s"):
        source = (PROGRAMS / name).read_text(encoding="utf-8")
        retires, mem = execute(assemble(source))
        insns = {r.insn for r in retires}
        assert EBREAK not in insns
        assert 0x00000073 not in insns
        assert _word(mem, 0x1004) == 0


def test_x0_and_fence_programs_retire_the_special_instructions() -> None:
    source = (PROGRAMS / "11_x0.s").read_text(encoding="utf-8")
    retires, _mem = execute(assemble(source))
    x0_writes = [r for r in retires if ((r.insn >> 7) & 31) == 0 and (r.insn & 0x7F) != 0x63]
    assert x0_writes
    assert all(r.rd == 0 and r.wdata == 0 for r in x0_writes)
    fence_src = (PROGRAMS / "12_fence.s").read_text(encoding="utf-8")
    fence_retires, _mem = execute(assemble(fence_src))
    assert any(r.insn == 0x0FF0000F and r.rd == 0 and r.wdata == 0 for r in fence_retires)
