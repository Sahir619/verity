"""Spec, plan and plan-gate stages (CONTRACTS 6.2 steps 1 to 4).

The plan gate is where a human may steer: pin a block option or toggle a variant, hold the countdown, or build
now. A pin or toggle replans once with the LLM (`plan.max_replans`), later ones are applied in code. In both cases
the steering is enforced in code afterwards, so a model that ignores a pin cannot change what the user asked for.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..contracts import (
    AutopilotSteer,
    GateSteer,
    PinInput,
    PinSteer,
    PlanBlock,
    SpecInput,
    ToggleInput,
    ToggleSteer,
    to_json,
)
from ..contracts.signoff import SpecRules
from ..core import captions as cap
from ..core.types import BudgetExhausted, LLMError, PlanOut, PlanResult, RoleOutputError
from ..roles.rtl_gen import PRIOR_HEADING, prior_text
from .feasibility import check_plan
from .plan_ops import apply_pin, apply_toggle, diff_blocks, find_block, find_variant, normalize_plan
from .state import RunState

log = logging.getLogger("verity.orchestrator")

GATE_POLL_S = 5.0
SPEC_RULES_PATH = "spec/rules.json"


class StageFailed(Exception):
    """A stage ended the run with an error. `announced` is True when its caption and stage event were already sent."""

    def __init__(self, reason: str, *, announced: bool = False) -> None:
        self.reason = reason
        self.announced = announced
        super().__init__(reason)


# ======================================================================
# spec
# ======================================================================
async def spec_stage(st: RunState) -> None:
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    await em.run("running")
    await em.human(SpecInput(t_ms=ctx.now_ms(), preset_id=pack.id, priority=ctx.priority))
    await em.spec(pack.to_spec(ctx.priority))
    await em.artifact(SPEC_RULES_PATH, to_json(SpecRules(rules=pack.spec_rules())))
    await em.add_node("spec", "spec", "plan", "Spec", ref_id="spec")
    await em.stage("spec", "done")
    await em.caption(cap.spec_read(pack.summary, ctx.priority), cap.CH_SPEC)
    told = ""
    if ctx.prior_attempt:  # a CPU core retry: exactly what the first RTL generation is told (rtl_gen v4)
        told = f"\n## {PRIOR_HEADING}\n\n{prior_text(ctx.prior_attempt)}\n"
    await st.save("spec.md", f"# {pack.title}\n\n{pack.spec_text}\n\nPriority: {ctx.priority}\n{told}")


# ======================================================================
# plan
# ======================================================================
async def _call_plan(st: RunState, previous: PlanOut | None, steering: list[PinInput | ToggleInput]) -> PlanResult:
    ctx = st.ctx
    # Known approaches from research and past runs: technique labels and code-written numbers only (claim 2).
    refs = ctx.references.planner_text() if ctx.references is not None else ""
    extra = {"references": refs} if refs else {}
    with ctx.busy():
        result = await ctx.roles.plan(ctx.llm, ctx.pack, ctx.priority, previous=previous, steering=steering, **extra)
    await st.credits()
    await st.record_meta(result.meta)
    return result


async def _publish_plan(st: RunState, plan: PlanOut, revision: int) -> None:
    body = plan.model_dump_json(by_alias=True, indent=2)
    await st.save(f"plan.r{revision}.json", body, "application/json")
    await st.em.artifact(f"plan/plan.r{revision}.json", body)


async def plan_stage(st: RunState) -> None:
    """Draft the plan, run the gate, apply the feasibility rules. Raises StageFailed to end the run."""
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    cfg = ctx.settings.plan

    await em.stage("plan", "active")
    await em.caption(cap.drafting_plan(), cap.CH_PLAN)

    result = await _call_plan(st, None, [])
    plan = normalize_plan(result.plan)
    st.plan, st.plan_revision = plan, 1
    await _publish_plan(st, plan, 1)
    await em.plan(1, plan.blocks)
    previous_id = "spec"
    for block in plan.blocks:
        await em.add_node(block.id, "block", "plan", block.name, ref_id=block.id)
        await em.connect(previous_id, block.id, "plan")
        previous_id = block.id

    await em.gate("open", cfg.gate_auto_continue_ms)
    await em.caption(cap.plan_drafted(len(plan.blocks), cfg.gate_auto_continue_ms), cap.CH_PLAN)
    await _gate_loop(st)
    await em.gate("closed", 0)

    reasons = check_plan(st.plan, pack, ctx.settings)
    if reasons:
        await em.stage("plan", "failed", detail=cap.clean(reasons[0], 120))
        await em.caption(cap.plan_rejected(reasons[0]), cap.CH_PLAN)
        raise StageFailed("plan rejected: " + cap.clean("; ".join(reasons), 300), announced=True)

    await em.stage("plan", "done")
    for block in st.plan.blocks:
        await em.update_status(block.id, "approved")


# ======================================================================
# gate
# ======================================================================
@dataclass(slots=True)
class _Gate:
    remaining_ms: int
    deadline: float = 0.0
    held: bool = False
    hold_until: float = 0.0
    replans_used: int = 0
    steering: list[PinInput | ToggleInput] = field(default_factory=list)

    def open(self) -> None:
        self.held = False
        self.deadline = time.monotonic() + self.remaining_ms / 1000

    def hold(self, max_hold_s: int) -> None:
        self.remaining_ms = max(0, int((self.deadline - time.monotonic()) * 1000))
        self.held = True
        self.hold_until = time.monotonic() + max_hold_s

    def expired(self) -> bool:
        return time.monotonic() >= (self.hold_until if self.held else self.deadline)

    def wait_s(self) -> float:
        return (self.hold_until if self.held else self.deadline) - time.monotonic()


async def _gate_loop(st: RunState) -> None:
    ctx, em = st.ctx, st.em
    cfg = ctx.settings.plan
    gate = _Gate(remaining_ms=cfg.gate_auto_continue_ms)
    gate.open()

    while not gate.expired():
        ctx.check_stop()
        try:
            with ctx.busy():  # a held or counting gate is a legitimate wait, not a stall
                request = await asyncio.wait_for(ctx.steer.get(), timeout=max(0.05, min(gate.wait_s(), GATE_POLL_S)))
        except TimeoutError:
            continue

        if isinstance(request, GateSteer):
            if request.action == "build_now":
                await em.caption(cap.building_now(), cap.CH_PLAN)
                return
            if request.action == "hold":
                await _hold(st, gate)
            elif request.action == "resume":
                await _resume(st, gate)
        elif isinstance(request, AutopilotSteer):
            if request.on:
                await _resume(st, gate)
            else:
                await _hold(st, gate)
        elif isinstance(request, PinSteer | ToggleSteer):
            await _steer_block(st, gate, request)
        # Other kinds (winner_override) cannot apply at the gate and are ignored.


async def _hold(st: RunState, gate: _Gate) -> None:
    if gate.held:
        return
    gate.hold(st.ctx.settings.plan.max_hold_s)
    await st.em.gate("held", gate.remaining_ms)
    await st.em.caption(cap.gate_held(), cap.CH_PLAN)


async def _resume(st: RunState, gate: _Gate) -> None:
    if not gate.held:
        return
    gate.open()
    await st.em.gate("open", gate.remaining_ms)


async def _steer_block(st: RunState, gate: _Gate, request: PinSteer | ToggleSteer) -> None:
    ctx, em = st.ctx, st.em
    assert st.plan is not None
    block = find_block(st.plan, request.block_id)
    if block is None:
        return

    if isinstance(request, PinSteer):
        human: PinInput | ToggleInput = PinInput(t_ms=ctx.now_ms(), block_id=block.id, pin=request.pin)
        caption = cap.pinned(block.name, request.pin)
        note = f"pinned {request.pin}"
    else:
        variant = find_variant(block, request.variant_id)
        if variant is None or request.value not in variant.options:
            return
        human = ToggleInput(t_ms=ctx.now_ms(), block_id=block.id, variant_id=variant.id, value=request.value)
        caption = cap.toggled(block.name, variant.label, request.value)
        note = f"{request.value} {variant.label.lower()}"

    await em.human(human)
    gate.steering.append(human)
    await em.update_status(block.id, "replanning")
    await em.caption(caption, cap.CH_PLAN)

    old = st.plan
    new = None
    if gate.replans_used < ctx.settings.plan.max_replans:
        gate.replans_used += 1
        try:
            new = normalize_plan((await _call_plan(st, old, list(gate.steering))).plan)
        except (BudgetExhausted, LLMError, RoleOutputError):
            log.warning("run %s: replan failed, applying the change in code", ctx.run_id, exc_info=True)
    if new is None:
        new = old
    new = _enforce_steering(new, gate.steering)

    revision = st.plan_revision + 1
    diff = diff_blocks(old.blocks, new.blocks)
    st.plan, st.plan_revision = new, revision
    await _publish_plan(st, new, revision)
    await em.plan(revision, new.blocks, diff, f"Replanned after your change: {note}.")

    await _update_canvas(st, old.blocks, new.blocks, steered_id=block.id, note=note)

    gate.remaining_ms = ctx.settings.plan.gate_after_replan_ms
    if gate.held:
        gate.hold_until = time.monotonic() + ctx.settings.plan.max_hold_s
        await em.gate("held", gate.remaining_ms)
    else:
        gate.open()
        await em.gate("open", gate.remaining_ms)
    await em.caption(cap.plan_updated(gate.remaining_ms), cap.CH_PLAN)


def _enforce_steering(plan: PlanOut, steering: list[PinInput | ToggleInput]) -> PlanOut:
    """Make sure every pin and toggle the user made is present in the plan, whatever the model returned."""
    for item in steering:
        if isinstance(item, PinInput):
            plan, _ = apply_pin(plan, item.block_id, item.pin)
        else:
            plan, _ = apply_toggle(plan, item.block_id, item.variant_id, item.value)
    return plan


async def _update_canvas(
    st: RunState, old: list[PlanBlock], new: list[PlanBlock], *, steered_id: str, note: str
) -> None:
    """Canvas ops after a replan: add and remove nodes, clear `replanning`, mark what changed."""
    em = st.em
    diff = diff_blocks(old, new)
    ids = [b.id for b in new]
    for block in new:
        if block.id in diff.added:
            i = ids.index(block.id)
            await em.add_node(block.id, "block", "plan", block.name, ref_id=block.id)
            await em.connect(ids[i - 1] if i > 0 else "spec", block.id, "plan")
            if i + 1 < len(ids) and ids[i + 1] not in diff.added:
                await em.connect(block.id, ids[i + 1], "plan")
    for block_id in diff.removed:
        await em.remove(block_id)
    for block in new:
        if block.id == steered_id:
            await em.update_status(block.id, "drafted", note=f"Changed: {note}")
        elif block.id in diff.changed:
            await em.update_status(block.id, "drafted", note="Changed: updated by the replan")
