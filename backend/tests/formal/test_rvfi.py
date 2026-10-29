"""Parser and wrapper generation for riscv-formal. No Docker and no solver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verity.core.pack import TaskPack
from verity.tools.base import SCRIPT_PATHS, RawOutput, SandboxCall, SandboxRunner
from verity.tools.rvfi import generate_wrapper, parse_checks_cfg, parse_rvfi

PACK = Path(__file__).resolve().parents[2] / "tasks" / "rv32i_core"
REQUIRED_DEPTHS = ("insn", "reg", "pc_fwd", "pc_bwd", "unique", "causal", "ill", "cover")
FORBIDDEN = ("liveness", "hang", "bus_", "csr")


class ScriptedRunner(SandboxRunner):
    def __init__(self, settings, payload: dict) -> None:
        super().__init__(settings, max_parallel=2)
        self.payload = payload
        self.calls: list[SandboxCall] = []

    async def _execute(self, call: SandboxCall) -> RawOutput:
        self.calls.append(call)
        return RawOutput(exit_code=0, stdout=json.dumps(self.payload) + "\n", stderr="", duration_ms=12)

    async def image_digest(self) -> str:
        return "sha256:scripted"


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def test_wrapper_file_matches_the_generator() -> None:
    text = _norm(generate_wrapper())
    committed = _norm((PACK / "formal" / "wrapper.sv").read_text(encoding="utf-8"))
    assert committed == text
    assert "module rvfi_wrapper" in text
    assert "rv32i_core dut" in text
    assert "wire rst_n = ~reset;" in text
    assert "`rvformal_rand_reg mem_ready;" in text
    assert "`rvformal_rand_reg [31:0] mem_rdata;" in text
    assert ".dbg_stall()," in text and "output dbg_stall" not in text
    assert ".halted()," in text
    for name, _width in (
        ("rvfi_valid", 1),
        ("rvfi_order", 64),
        ("rvfi_trap", 1),
        ("rvfi_halt", 1),
        ("rvfi_mem_rmask", 4),
        ("rvfi_mem_wmask", 4),
        ("rvfi_rd_wdata", 32),
    ):
        assert name in text


def test_checks_cfg_is_rv32i_nret1_without_liveness() -> None:
    sections = parse_checks_cfg((PACK / "formal" / "checks.cfg").read_text(encoding="utf-8"))
    options = sections["options"]
    assert "isa rv32i" in options and "nret 1" in options
    names = [line.split()[0] for line in sections["depth"]]
    assert names == list(REQUIRED_DEPTHS)
    depths = {line.split()[0]: line.split()[1:] for line in sections["depth"]}
    assert depths["insn"] == ["15"]
    assert depths["unique"] == ["1", "10", "20"]
    assert depths["reg"] == ["1", "20"]
    assert depths["ill"] == ["15"]
    blob = "\n".join(line for lines in sections.values() for line in lines)
    for token in FORBIDDEN:
        assert token not in blob
    assert "RISCV_FORMAL_ALIGNED_MEM" in blob
    assert "channel[0].cnt_insns == 2" in "\n".join(sections["cover"])
    assert "@basedir@/cores/@core@/wrapper.sv" in sections["verilog-files"]
    assert "@basedir@/cores/@core@/dut.v" in sections["verilog-files"]


def test_parser_keeps_an_honest_pass_and_a_counterexample() -> None:
    payload = {
        "schema": "verity.rvfi/1",
        "status": "fail",
        "passed": 1,
        "total": 2,
        "duration_ms": 40,
        "message": "insn_lw_ch0: assert",
        "checks": [
            {"name": "insn_addi_ch0", "status": "pass", "seconds": 1.5},
            {
                "name": "insn_lw_ch0",
                "status": "fail",
                "seconds": 3.25,
                "trace": "rvfi/insn_lw_ch0/trace.vcd",
                "message": "assert mem_rmask",
            },
        ],
    }
    result = parse_rvfi(payload)
    assert result.status == "fail" and result.passed == 1 and result.total == 2
    assert result.checks[1].trace == "rvfi/insn_lw_ch0/trace.vcd"
    assert result.checks[1].message == "assert mem_rmask"


def test_a_pass_that_hides_a_failure_is_downgraded() -> None:
    hidden = {
        "schema": "verity.rvfi/1",
        "status": "ok",
        "passed": 2,
        "total": 2,
        "checks": [
            {"name": "insn_addi_ch0", "status": "pass", "seconds": 1},
            {"name": "insn_lw_ch0", "status": "fail", "seconds": 2, "trace": "rvfi/insn_lw_ch0/trace.vcd", "message": "assert"},
        ],
    }
    result = parse_rvfi(hidden)
    assert result.status == "fail" and result.passed == 1 and result.total == 2
    timed = {
        "schema": "verity.rvfi/1",
        "status": "ok",
        "passed": 1,
        "total": 1,
        "checks": [{"name": "insn_addi_ch0", "status": "timeout", "seconds": 180, "message": "timed out"}],
    }
    assert parse_rvfi(timed).status == "timeout"
    with pytest.raises(ValidationError):
        parse_rvfi({"schema": "verity.rvfi/1", "status": "pass", "checks": []})


async def test_rvfi_call_uses_the_script_and_appends_blocks(settings, packs: dict[str, TaskPack]) -> None:
    pack = packs["rv32i_core"]
    rtl = pack.validate_rtl(pack.reference_rtl() or "")
    payload = {
        "schema": "verity.rvfi/1",
        "status": "ok",
        "passed": 1,
        "total": 1,
        "checks": [{"name": "insn_addi_ch0", "status": "pass", "seconds": 1.0}],
    }
    runner = ScriptedRunner(settings, payload)
    result = await runner.rvfi(rtl, pack)
    (call,) = runner.calls
    assert call.script == "rvfi"
    assert SCRIPT_PATHS["rvfi"] == "/verity/bin/rvfi"
    assert "VERITY_RVFI_CHECK_TIMEOUT_S" in call.env and "VERITY_RVFI_JOBS" in call.env
    dut = call.work_files["dut.v"].decode("utf-8")
    assert "module rv32i_core" in dut and "module rv32i_ctl" in dut
    assert result.status == "ok" and result.passed == 1 and result.total == 1
