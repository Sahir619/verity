"""Feasibility gate (ARCHITECTURE 10.2): code decides, before any RTL token is spent."""

import pytest

from verity.contracts import PlanBlock, Variant
from verity.core.pack import TaskPack
from verity.core.types import PlanOut
from verity.orchestrator.feasibility import check_plan


def good_plan(pack: TaskPack) -> PlanOut:
    return PlanOut(
        summary="A small plan",
        blocks=[
            PlanBlock(id="blk-in", name="Input registers", reason="Capture the inputs.", pin_options=["Minimize registers"]),
            PlanBlock(
                id="blk-mult",
                name="Multiplier array",
                reason="One multiplier per lane.",
                variants=[Variant(id="pipeline", label="Pipeline", options=["none", "2-stage"], value="none")],
            ),
            PlanBlock(id="blk-acc", name="Accumulator", reason="Keep the running sum."),
        ],
        latency_cycles=pack.latency_cycles,
        estimated_lines=120,
    )


def test_a_reasonable_plan_passes(settings, mac4: TaskPack) -> None:
    assert check_plan(good_plan(mac4), mac4, settings) == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"latency_cycles": 3}, "latency"),
        ({"estimated_lines": 5_000}, "lines"),
        ({"estimated_lines": 0}, "line estimate"),
        ({"blocks": []}, "no blocks"),
    ],
)
def test_plan_level_rejections(settings, mac4: TaskPack, change: dict, fragment: str) -> None:
    plan = good_plan(mac4).model_copy(update=change)
    reasons = check_plan(plan, mac4, settings)
    assert any(fragment in r for r in reasons), reasons


def test_too_many_blocks(settings, mac4: TaskPack) -> None:
    blocks = [PlanBlock(id=f"blk-{i}", name=f"Block {i}", reason="r") for i in range(mac4.max_plan_blocks + 1)]
    reasons = check_plan(good_plan(mac4).model_copy(update={"blocks": blocks}), mac4, settings)
    assert any("blocks, the limit" in r for r in reasons)


def test_out_of_scope_and_inconsistent_blocks(settings, mac4: TaskPack) -> None:
    plan = good_plan(mac4)
    bad = plan.blocks[:]
    bad.append(PlanBlock(id="blk-pll", name="PLL clock generator", reason="Needs a PLL."))
    bad.append(PlanBlock(id="blk-in", name="Duplicate", reason="same id"))
    bad.append(
        PlanBlock(id="blk-var", name="Variant", reason="r", variants=[Variant(id="v", label="V", options=["a"], value="zzz")])
    )
    reasons = check_plan(plan.model_copy(update={"blocks": bad}), mac4, settings)
    joined = " | ".join(reasons)
    assert "outside the supported toolchain" in joined
    assert "duplicate block id" in joined
    assert "not one of its options" in joined


def test_risks_text_may_mention_unsupported_things(settings, mac4: TaskPack) -> None:
    plan = good_plan(mac4).model_copy(update={"risks": ["no PLL is needed", "no SRAM either"]})
    assert check_plan(plan, mac4, settings) == []
