"""Tests for CircuitOptimizer."""

from verity.core.pack import TaskPack
from verity.engine.optimizer import CircuitOptimizer


def test_optimize_mac4_speed(packs: dict[str, TaskPack]) -> None:
    pack = packs["mac4"]
    opt = CircuitOptimizer()
    res = opt.optimize(pack, priority="speed")

    assert res.priority == "speed"
    assert "carry-save" in res.strategy.lower()
    # Check that Verilog validates cleanly against task pack coding rules
    rtl_obj = pack.validate_rtl(res.verilog)
    assert rtl_obj.top == "mac4"
    assert "sum_pair0" in res.verilog
    assert "sum_pair1" in res.verilog


def test_optimize_mac4_area(packs: dict[str, TaskPack]) -> None:
    pack = packs["mac4"]
    opt = CircuitOptimizer()
    res = opt.optimize(pack, priority="area")

    assert res.priority == "area"
    rtl_obj = pack.validate_rtl(res.verilog)
    assert rtl_obj.top == "mac4"
