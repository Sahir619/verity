"""cocotb testbench for rv32i_cache_core.

Two cases per program in programs/*.s, at wait 0 and wait 3. The testbench is the memory: on a falling edge, a mem_valid
request with mem_ready low waits W falling edges, then is served for one cycle (mem_rdata is the word before the write).
Each rvfi_valid record with rvfi_trap clear is compared with golden.execute, in order. A record
with rvfi_trap set is the halt, never a retire. The core must then halt with dbg_stall 3 and no
further rvfi_valid. Named `# expect` words are compared with this testbench memory.

Inputs change and outputs are sampled only on the falling edge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cocotb
import golden
from cocotb.triggers import FallingEdge, RisingEdge
from verity_tb import PASSED, Outcome, Recorder, env_int, run_cases, start_clock

import rv32i_asm

TOP = "rv32i_cache_core"
WORDS = 16 * 1024
MAX_CYCLES = env_int("VERITY_MAX_CYCLES", 20000)
_LOADS = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4}
_STORES = {"sb": 1, "sh": 2, "sw": 4}
_RS1_OPS = {0x13, 0x33, 0x03, 0x23, 0x63, 0x67}
_RS2_OPS = {0x33, 0x23, 0x63}


@dataclass
class Case:
    name: str
    inputs: str
    image: dict
    retires: list
    expects: list
    wait_cycles: int


def _pack_dir() -> Path:
    env = os.environ.get("VERITY_PACK_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent


def _load_cases() -> list[Case]:
    cases: list[Case] = []
    for path in sorted((_pack_dir() / "programs").glob("*.s")):
        source = path.read_text(encoding="utf-8")
        image = rv32i_asm.assemble(source)
        retires, _mem = golden.execute(image)
        expects = rv32i_asm.parse_expects(source)
        for wait in (0, 3):
            cases.append(Case(f"{path.stem}_wait{wait}", path.name, image, retires, expects, wait))
    return cases


def _head(index: int, exp) -> str:
    return (
        f"first mismatch: index {index} "
        f"pc=0x{exp.pc:08x} insn=0x{exp.insn:08x} ({golden.disassemble(exp.insn)}) "
        f"expected rd={exp.rd} wdata=0x{exp.wdata:08x}"
    )


def _mismatch(index: int, exp, got_pc: int, got_insn: int, got_rd: int, got_wdata: int, note: str = "") -> str:
    text = (
        f"{_head(index, exp)} "
        f"actual pc=0x{got_pc:08x} insn=0x{got_insn:08x} ({golden.disassemble(got_insn)}) "
        f"rd={got_rd} wdata=0x{got_wdata:08x}"
    )
    return f"{text} {note}" if note else text


def _extra(index: int, got_pc: int, got_insn: int, got_rd: int, got_wdata: int) -> str:
    return (
        f"first mismatch: index {index} expected no retire "
        f"actual pc=0x{got_pc:08x} insn=0x{got_insn:08x} ({golden.disassemble(got_insn)}) "
        f"rd={got_rd} wdata=0x{got_wdata:08x}"
    )


def _missing(index: int, exp) -> str:
    return f"{_head(index, exp)} halted before the retire"


def _lane_mask(size: int, lo: int) -> int:
    if size == 1:
        return 1 << lo
    if size == 2:
        return 0x3 << lo
    if size == 4:
        return 0xF
    return 0


def _byte(word: int, lane: int) -> int:
    return (word >> (8 * lane)) & 0xFF


async def _reset(dut) -> None:
    dut.rst_n.value = 0
    dut.mem_ready.value = 0
    dut.mem_rdata.value = 0
    await FallingEdge(dut.clk)
    for _ in range(3):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    dut.mem_ready.value = 0


def _apply(mem: list[int], addr: int, wstrb: int, wdata: int) -> int:
    idx = (addr >> 2) & (WORDS - 1)
    old = mem[idx]
    new = old
    for lane in range(4):
        if wstrb & (1 << lane):
            shift = 8 * lane
            mask = 0xFF << shift
            new = (new & ~mask) | (wdata & mask)
    mem[idx] = new & 0xFFFFFFFF
    return old & 0xFFFFFFFF


def _sample(dut) -> dict | str:
    names = {
        "valid": (dut.rvfi_valid, 1),
        "order": (dut.rvfi_order, 64),
        "insn": (dut.rvfi_insn, 32),
        "trap": (dut.rvfi_trap, 1),
        "halt": (dut.rvfi_halt, 1),
        "intr": (dut.rvfi_intr, 1),
        "mode": (dut.rvfi_mode, 2),
        "ixl": (dut.rvfi_ixl, 2),
        "rs1_addr": (dut.rvfi_rs1_addr, 5),
        "rs2_addr": (dut.rvfi_rs2_addr, 5),
        "rs1_rdata": (dut.rvfi_rs1_rdata, 32),
        "rs2_rdata": (dut.rvfi_rs2_rdata, 32),
        "rd": (dut.rvfi_rd_addr, 5),
        "wdata": (dut.rvfi_rd_wdata, 32),
        "pc": (dut.rvfi_pc_rdata, 32),
        "pc_wdata": (dut.rvfi_pc_wdata, 32),
        "mem_addr": (dut.rvfi_mem_addr, 32),
        "rmask": (dut.rvfi_mem_rmask, 4),
        "wmask": (dut.rvfi_mem_wmask, 4),
        "rdata": (dut.rvfi_mem_rdata, 32),
        "mwdata": (dut.rvfi_mem_wdata, 32),
        "halted": (dut.halted, 1),
        "stall": (dut.dbg_stall, 2),
    }
    got = {}
    for key, (handle, width) in names.items():
        value = _bits(handle, width)
        if value is None:
            return f"{key} was X"
        got[key] = value
    return got


def _check_stall(dut, got: dict, ready: int, cycle: int) -> str | None:
    """dbg_stall follows the pins this testbench can see.

    A waiting request (mem_valid, mem_ready still low) is fetch or data. A store wait
    has a write strobe, so it must be data. Any other cycle is busy, and halt is 3.
    """
    if got["halted"] == 1:
        if got["stall"] != 3:
            return f"dbg_stall was {got['stall']} while halted"
        return None
    mem_valid = _bit(dut.mem_valid)
    wstrb = _bits(dut.mem_wstrb, 4)
    if mem_valid is None or wstrb is None:
        return "mem_valid or mem_wstrb was X"
    if mem_valid == 1 and ready == 0:
        if got["stall"] not in (1, 2):
            return f"dbg_stall was {got['stall']} while a memory request waited"
        if wstrb != 0 and got["stall"] != 2:
            return f"dbg_stall was {got['stall']} while a store waited"
        return None
    if got["stall"] != 0:
        return f"dbg_stall was {got['stall']} while the core was busy at cycle {cycle}"
    return None


def _check_consts(got: dict, index: int, exp) -> str | None:
    if got["intr"] != 0 or got["mode"] != 3 or got["ixl"] != 1:
        return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], "rvfi_intr, rvfi_mode or rvfi_ixl")
    if got["order"] != index:
        return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], f"rvfi_order={got['order']}")
    return None


def _check_rs(got: dict, insn: int, regs: list[int], index: int, exp) -> str | None:
    op = insn & 0x7F
    for addr, data, field, used in (
        (got["rs1_addr"], got["rs1_rdata"], 15, op in _RS1_OPS),
        (got["rs2_addr"], got["rs2_rdata"], 20, op in _RS2_OPS),
    ):
        if addr == 0 and data != 0:
            return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], "x0 rdata was not 0")
        if addr != 0 and data != regs[addr]:
            return _mismatch(
                index, exp, got["pc"], got["insn"], got["rd"], got["wdata"],
                f"rs rdata 0x{data:08x} expected 0x{regs[addr]:08x}",
            )
        want = (insn >> field) & 31
        if used and addr != want:
            return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], f"rs addr {addr} expected {want}")
    return None


def _check_mem(got: dict, insn: int, regs: list[int], mem: list[int], index: int, exp) -> str | None:
    name = golden._classify(insn)
    if name in _LOADS:
        addr = (regs[(insn >> 15) & 31] + golden._i_imm(insn)) & 0xFFFFFFFF
        size = _LOADS[name]
        want_mask, want_word, write = _lane_mask(size, addr & 3), mem[(addr >> 2) & (WORDS - 1)], False
    elif name in _STORES:
        addr = (regs[(insn >> 15) & 31] + golden._s_imm(insn)) & 0xFFFFFFFF
        size = _STORES[name]
        want_mask = _lane_mask(size, addr & 3)
        want_word = (regs[(insn >> 20) & 31] << (8 * (addr & 3))) & 0xFFFFFFFF
        write = True
    else:
        addr, want_mask, want_word, write = 0, 0, 0, False
    if name in _LOADS or name in _STORES:
        if got["mem_addr"] != (addr & ~3):
            return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], f"mem addr 0x{got['mem_addr']:08x}")
    got_mask = got["wmask"] if write else got["rmask"]
    other = got["rmask"] if write else got["wmask"]
    if name not in _LOADS and name not in _STORES:
        if got["rmask"] or got["wmask"]:
            return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], "memory mask was not 0")
        return None
    if got_mask != want_mask or other != 0:
        return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], f"mem mask 0x{got_mask:x} expected 0x{want_mask:x}")
    data = got["mwdata"] if write else got["rdata"]
    for lane in range(4):
        if want_mask & (1 << lane) and _byte(data, lane) != _byte(want_word, lane):
            return _mismatch(index, exp, got["pc"], got["insn"], got["rd"], got["wdata"], f"mem byte {lane}")
    return None


async def _run_case(dut, case: Case) -> Outcome:
    if not case.expects:
        text = f"{case.name}: program names at least one result word"
        return Outcome(False, text, "no expect lines", 0, case.name)
    mem = [0] * WORDS
    for addr, word in case.image.items():
        mem[(addr >> 2) & (WORDS - 1)] = word & 0xFFFFFFFF
    regs = [0] * 32
    await _reset(dut)
    seen = 0
    ready = 0
    halt_cycle = 0
    trap_seen = False
    waiting = 0
    external_requests = 0
    logical_requests = 0
    last_pc_wdata = None
    for cycle in range(1, MAX_CYCLES + 1):
        await FallingEdge(dut.clk)
        got = _sample(dut)
        if isinstance(got, str):
            text = "rvfi fields, halted and dbg_stall are known"
            return Outcome(False, text, got, cycle, case.name)
        if got["stall"] > 3:
            text = "dbg_stall is 0, 1, 2 or 3"
            return Outcome(False, text, f"dbg_stall={got['stall']}", cycle, case.name)
        note = _check_stall(dut, got, ready, cycle)
        if note is not None:
            return Outcome(False, note, note, cycle, case.name)
        if got["valid"] == 1:
            logical_requests += 1
            if not got["trap"] and (got["insn"] & 0x7F) in (0x03, 0x23):
                logical_requests += 1
            if trap_seen:
                text = _extra(seen, got["pc"], got["insn"], got["rd"], got["wdata"])
                return Outcome(False, text, "rvfi_valid after the trap", cycle, case.name)
            if got["trap"] == 1:
                if got["halt"] != 1 or got["rd"] != 0 or got["wdata"] != 0 or got["rmask"] != 0 or got["wmask"] != 0:
                    text = "trap record has rvfi_halt, rd 0, wdata 0 and no memory masks"
                    return Outcome(False, text, text, cycle, case.name)
                if seen != len(case.retires) or got["order"] != seen:
                    exp = case.retires[seen] if seen < len(case.retires) else None
                    text = _missing(seen, exp) if exp is not None else "trap order does not follow the retires"
                    return Outcome(False, text, text, cycle, case.name)
                if last_pc_wdata is None:
                    if got["pc"] != 0:
                        text = f"first mismatch: trap pc=0x{got['pc']:08x} expected 0"
                        return Outcome(False, text, text, cycle, case.name)
                elif got["pc"] != last_pc_wdata:
                    text = f"first mismatch: trap pc=0x{got['pc']:08x} expected 0x{last_pc_wdata:08x}"
                    return Outcome(False, text, text, cycle, case.name)
                if got["pc_wdata"] != got["pc"] or got["intr"] != 0 or got["mode"] != 3 or got["ixl"] != 1:
                    text = "trap record keeps the pc and reports machine mode"
                    return Outcome(False, text, text, cycle, case.name)
                trap_seen = True
            else:
                pc, insn, rd, wdata = got["pc"], got["insn"], got["rd"], got["wdata"]
                if seen >= len(case.retires):
                    text = _extra(seen, pc, insn, rd, wdata)
                    return Outcome(False, text, text, cycle, case.name)
                exp = case.retires[seen]
                if (pc, insn, rd, wdata) != (exp.pc, exp.insn, exp.rd, exp.wdata):
                    text = _mismatch(seen, exp, pc, insn, rd, wdata)
                    return Outcome(False, text, text, cycle, case.name)
                if got["trap"] != 0 or got["halt"] != 0:
                    text = _mismatch(seen, exp, pc, insn, rd, wdata, "rvfi_trap or rvfi_halt was 1")
                    return Outcome(False, text, text, cycle, case.name)
                note = _check_consts(got, seen, exp) or _check_rs(got, insn, regs, seen, exp)
                if note is None and last_pc_wdata is not None and pc != last_pc_wdata:
                    note = _mismatch(seen, exp, pc, insn, rd, wdata, f"pc_wdata chain expected 0x{last_pc_wdata:08x}")
                if note is None:
                    note = _check_mem(got, insn, regs, mem, seen, exp)
                if note is not None:
                    return Outcome(False, note, note, cycle, case.name)
                if rd != 0:
                    regs[rd] = wdata
                seen += 1
            last_pc_wdata = got["pc_wdata"]
        if got["halted"] == 1:
            if not trap_seen:
                text = "halted without a trap record"
                return Outcome(False, text, text, cycle, case.name)
            if seen < len(case.retires):
                text = _missing(seen, case.retires[seen])
                return Outcome(False, text, text, cycle, case.name)
            if got["stall"] != 3:
                text = f"dbg_stall was {got['stall']} while halted"
                return Outcome(False, text, text, cycle, case.name)
            if got["valid"] == 1:
                text = "rvfi_valid stayed 1 after the trap"
                return Outcome(False, text, text, cycle, case.name)
            halt_cycle = cycle
            break
        served = _serve(dut, mem, ready, waiting, case.wait_cycles)
        if isinstance(served, Outcome):
            return Outcome(served.ok, served.expected, served.got, cycle, case.name)
        ready, waiting = served
        external_requests += ready
    else:
        text = f"halted was 0 after {MAX_CYCLES} cycles"
        return Outcome(False, text, text, MAX_CYCLES, case.name)
    for addr, expect in case.expects:
        got_word = mem[(addr >> 2) & (WORDS - 1)]
        if got_word != expect:
            text = f"first mismatch: memory 0x{addr:08x} expected 0x{expect:08x} actual 0x{got_word:08x}"
            return Outcome(False, text, text, halt_cycle, case.name)
    if case.wait_cycles == 3 and case.inputs in ("01_sum1to10.s", "02_fibonacci.s", "03_array_sum.s"):
        # Every retired/trapping instruction fetched once. Only legal loads/stores add a data request.
        # Subtract acknowledged external requests to count cache hits using public pins only.
        hits = logical_requests - external_requests
        dut._log.info("CACHE_HITS program=%s wait=3 requests=%d external=%d hits=%d",
                      case.inputs, logical_requests, external_requests, hits)
    return PASSED


def _serve(dut, mem: list[int], ready: int, waiting: int, wait_cycles: int):
    mem_valid = _bit(dut.mem_valid)
    if mem_valid is None:
        return Outcome(False, "mem_valid is 0 or 1", "mem_valid was X")
    if mem_valid == 1 and ready == 0:
        if waiting < wait_cycles:
            dut.mem_ready.value = 0
            return 0, waiting + 1
        addr = _word(dut.mem_addr)
        wstrb = _bits(dut.mem_wstrb, 4)
        wdata = _word(dut.mem_wdata)
        if None in (addr, wstrb, wdata):
            return Outcome(False, "memory request fields are known", "a memory request field was X")
        dut.mem_rdata.value = _apply(mem, addr, wstrb, wdata)
        dut.mem_ready.value = 1
        return 1, 0
    dut.mem_ready.value = 0
    return 0, 0


def _bit(handle):
    return _bits(handle, 1)


def _word(handle):
    return _bits(handle, 32)


def _bits(handle, width: int):
    from verity_tb import read_int

    value = read_int(handle)
    if value is None:
        return None
    return value & ((1 << width) - 1)


@cocotb.test()
async def run_all(dut):
    dut.rst_n.value = 0
    dut.mem_ready.value = 0
    dut.mem_rdata.value = 0
    start_clock(dut, "clk")
    rec = Recorder(TOP)
    await run_cases(rec, _load_cases(), lambda case: _run_case(dut, case))
