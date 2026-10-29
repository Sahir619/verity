"""Plan block helpers and feasibility rules. Written, not yet run."""

from verity.contracts import PlanBlock, Variant
from verity.core.types import PlanOut
from verity.orchestrator.plan_ops import apply_pin, apply_toggle, diff_blocks, normalize_plan, slug_block_id


def make_plan() -> PlanOut:
    return PlanOut(
        summary="s",
        blocks=[
            PlanBlock(id="Input Regs!", name="Input registers", reason="r", pin_options=["Minimize registers"]),
            PlanBlock(
                id="blk-mult",
                name="Multiplier",
                reason="r",
                pin_options=["Minimize area"],
                variants=[Variant(id="pipeline", label="Pipeline", options=["none", "2-stage"], value="none")],
            ),
            PlanBlock(id="blk-mult", name="Duplicate id", reason="r"),
        ],
        latency_cycles=16,
        estimated_lines=100,
    )


def test_slug_is_safe_and_unique() -> None:
    taken: set[str] = set()
    assert slug_block_id("Input Regs!", taken) == "blk-input-regs"
    assert slug_block_id("input regs", taken) == "blk-input-regs-2"
    assert slug_block_id("", taken) == "blk-block"


def test_normalize_makes_ids_unique() -> None:
    plan = normalize_plan(make_plan())
    ids = [b.id for b in plan.blocks]
    assert ids == ["blk-input-regs", "blk-mult", "blk-mult-2"]


def test_diff() -> None:
    old = normalize_plan(make_plan())
    new, _ = apply_toggle(old, "blk-mult", "pipeline", "2-stage")
    new = new.model_copy(update={"blocks": new.blocks[:2]})
    diff = diff_blocks(old.blocks, new.blocks)
    assert diff.changed == ["blk-mult"]
    assert diff.removed == ["blk-mult-2"]
    assert diff.added == []


def test_apply_pin_and_toggle_in_code() -> None:
    plan = normalize_plan(make_plan())
    pinned, note = apply_pin(plan, "blk-mult", "Minimize area")
    assert note == "pinned Minimize area"
    assert pinned.blocks[1].pins == ["Minimize area"]
    again, note2 = apply_pin(pinned, "blk-mult", "Minimize area")
    assert note2 is None and again == pinned
    unknown, note3 = apply_pin(plan, "blk-mult", "Not an option")
    assert note3 is None and unknown == plan
    toggled, note4 = apply_toggle(plan, "blk-mult", "pipeline", "2-stage")
    assert note4 == "2-stage pipeline"
    assert toggled.blocks[1].variants[0].value == "2-stage"
