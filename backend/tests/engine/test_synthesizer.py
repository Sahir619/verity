"""Tests for PlanSynthesizer on all supported Verity task packs."""

import pytest

from verity.core.pack import TaskPack
from verity.engine.synthesizer import PlanSynthesizer


def test_synth_mac4(packs: dict[str, TaskPack]) -> None:
    pack = packs["mac4"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    # Validate against Verity coding rules and port contracts
    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "mac4"
    assert "lane_sum" in code
    assert "acc <= 22'd0;" in code
    assert "done <= 1'b1;" in code


def test_synth_alu8(packs: dict[str, TaskPack]) -> None:
    pack = packs["alu8"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "alu8"
    assert "case (op)" in code
    assert "next_y" in code
    assert "next_zero" in code
    assert "next_carry" in code


def test_synth_add8(packs: dict[str, TaskPack]) -> None:
    pack = packs["add8"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "add8"
    assert "assign {cout, sum} = ((a + b) + cin);" in code
    assert "always" not in code  # purely combinational


def test_synth_cnt8(packs: dict[str, TaskPack]) -> None:
    pack = packs["cnt8"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "cnt8"
    assert "if (!rst_n) begin" in code
    assert "q <= 8'd0;" in code
    assert "q <= (q + 8'd1);" in code


def test_synth_fsm_seq(packs: dict[str, TaskPack]) -> None:
    pack = packs["fsm_seq"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "fsm_seq"
    assert "history" in code
    assert "4'b1011" in code


def test_synth_matmul4(packs: dict[str, TaskPack]) -> None:
    pack = packs["matmul4"]
    synth = PlanSynthesizer()
    code = synth.synthesize_verilog(pack, priority="area")

    rtl_obj = pack.validate_rtl(code)
    assert rtl_obj.top == "matmul4"
    assert "A_0" in code
    assert "B_0" in code
    assert "cnt == 5'd24" in code
    assert "done <= 1'b1;" in code
