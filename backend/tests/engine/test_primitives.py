"""Tests for parametric hardware primitives."""

from verity.engine.ast import Ident
from verity.engine.primitives import (
    bits_needed,
    build_balanced_adder_tree,
    build_cycle_counter,
    build_parallel_multipliers,
    build_pipeline_stage,
    unpack_lane,
)


def test_bits_needed() -> None:
    assert bits_needed(0) == 1
    assert bits_needed(1) == 1
    assert bits_needed(2) == 2
    assert bits_needed(3) == 2
    assert bits_needed(4) == 3
    assert bits_needed(15) == 4
    assert bits_needed(16) == 5
    assert bits_needed(255) == 8


def test_unpack_lane() -> None:
    lane0 = unpack_lane("a", 0, 8)
    assert lane0.expr == Ident("a")
    assert lane0.base == "0"
    assert lane0.width == 8

    lane3 = unpack_lane("b", 3, 8)
    assert lane3.base == "24"
    assert lane3.width == 8


def test_build_parallel_multipliers() -> None:
    items, signals = build_parallel_multipliers(
        a_name="a",
        b_name="b",
        lane_count=4,
        lane_width=8,
        signed=False,
    )
    assert len(items) == 4
    assert len(signals) == 4
    assert signals[0].name == "p0"
    assert signals[3].name == "p3"


def test_build_balanced_adder_tree() -> None:
    inputs = [Ident(f"in_{i}") for i in range(4)]
    items, final_sum = build_balanced_adder_tree(inputs, input_width=16)

    # 4 inputs -> 2 pairs at stage 0, 1 pair at stage 1 -> 3 total adder wires
    assert len(items) == 3
    assert final_sum == Ident("sum_stage_1_0")


def test_build_cycle_counter() -> None:
    reg, width, reset_stmt, inc_stmt, terminal_cond = build_cycle_counter(16, "cnt")
    assert reg.name == "cnt"
    assert width == 4  # 0 to 15 needs 4 bits
    assert terminal_cond.op == "=="


def test_build_pipeline_stage() -> None:
    reg, reset_stmt, update_stmt, out_id = build_pipeline_stage("stage1", 16, Ident("data_in"))
    assert reg.name == "stage1"
    assert reg.width == 16
    assert out_id.name == "stage1"
