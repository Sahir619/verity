"""Pydantic mirror of frontend/src/lib/contracts/types.ts (and the zod schemas in schemas.ts).

Field names are snake_case here and camelCase on the wire (alias generator in base.py).
Do not add, rename or remove fields without changing types.ts and schemas.ts in the same change.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field, TypeAdapter

from .base import WireModel, opt, to_json, to_wire

# ---------- enums ----------
Priority = Literal["area", "speed", "power"]
Operator = Literal["FIX", "SIMPLIFY", "OPTIMIZE", "EXPLORE"]
CreatedBy = Literal["INIT", "SIMPLIFY", "OPTIMIZE", "EXPLORE"]
AttemptBy = Literal["INIT", "SIMPLIFY", "OPTIMIZE", "EXPLORE", "FIX"]
StageId = Literal["spec", "plan", "lock", "rtl", "lint", "sim", "fix", "synth", "evolve", "prove"]
StageStatus = Literal["pending", "active", "done", "failed", "skipped"]
CandidateStatus = Literal[
    "drafting", "linting", "simulating", "fail", "fixing", "pass", "synthesizing", "measured", "rejected"
]
RunMode = Literal["replay", "live"]
RunPhase = Literal["idle", "starting", "running", "done", "error", "cap_reached", "stopped"]
ZoneId = Literal["plan", "tree"]
NodeKind = Literal["spec", "block", "golden", "candidate"]
EdgeKind = Literal["plan", "lineage", "checks", "proof"]
Actor = Literal["ai", "user", "system"]
PortDir = Literal["in", "out"]
LintStatus = Literal["pass", "fail"]
GateState = Literal["open", "held", "closed"]
EqyStatus = Literal["proven", "not_equivalent", "inconclusive", "not_run"]
WinnerBy = Literal["ai", "user"]

STAGE_ORDER: tuple[StageId, ...] = ("spec", "plan", "lock", "rtl", "lint", "sim", "fix", "synth", "evolve", "prove")


# ---------- spec and plan ----------
class Port(WireModel):
    name: str
    dir: PortDir
    width: int
    count: int | None = opt()


class Spec(WireModel):
    preset_id: str
    title: str
    text: str
    ports: list[Port]
    priority: Priority
    test_count: int
    max_lines: int


class Variant(WireModel):
    id: str
    label: str
    options: list[str]
    value: str


class PlanBlock(WireModel):
    id: str
    name: str
    reason: str
    pin_options: list[str] = Field(default_factory=list)
    pins: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)


class PlanDiff(WireModel):
    added: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


# ---------- verification ----------
class TestCase(WireModel):
    __test__ = False  # keep pytest from collecting this class

    name: str
    inputs: str
    expected: str
    got: str
    pass_: bool
    cycle: int | None = opt()


class Verification(WireModel):
    lint: LintStatus
    lint_log: str | None = opt()
    passed: int
    total: int
    failures: list[TestCase] = Field(default_factory=list)
    diagnosis: str | None = opt()
    tool: str
    duration_ms: int


class Attempt(WireModel):
    n: int
    by: AttemptBy
    reason: str
    rtl: str
    verification: Verification


# ---------- PPA, fitness, proof ----------
class PPA(WireModel):
    area_um2: float | None = None
    cell_count: int | None = None
    clock_period_ns: float | None = None
    power_mw: float | None = None
    tools: list[str] = Field(default_factory=list)
    pdk: str


class FitnessWeights(WireModel):
    area: float
    timing: float
    power: float


class FitnessGains(WireModel):
    area: float | None = None
    timing: float | None = None
    power: float | None = None


class Fitness(WireModel):
    score: float
    priority: Priority
    weights: FitnessWeights
    gains: FitnessGains
    baseline_id: str


class Equivalence(WireModel):
    status: EqyStatus
    against_id: str
    tool: Literal["yosys-eqy"] = "yosys-eqy"
    log: str | None = opt()


# ---------- candidate ----------
class Candidate(WireModel):
    id: str
    label: str
    round: int
    parent_id: str | None = None
    created_by: CreatedBy
    reason: str
    rtl: str
    attempts: list[Attempt] = Field(default_factory=list)
    verification: Verification | None = None
    ppa: PPA | None = None
    fitness: Fitness | None = None
    status: CandidateStatus
    fix_count: int = 0
    is_baseline: bool = False
    is_elite: bool = False
    eqy: Equivalence | None = opt()


# ---------- human inputs and credits ----------
class SpecInput(WireModel):
    kind: Literal["spec"] = "spec"
    t_ms: int
    preset_id: str
    priority: Priority


class PinInput(WireModel):
    kind: Literal["pin"] = "pin"
    t_ms: int
    block_id: str
    pin: str


class ToggleInput(WireModel):
    kind: Literal["toggle"] = "toggle"
    t_ms: int
    block_id: str
    variant_id: str
    value: str


class WinnerOverrideInput(WireModel):
    kind: Literal["winner_override"] = "winner_override"
    t_ms: int
    candidate_id: str
    local: bool | None = opt()


class StopInput(WireModel):
    kind: Literal["stop"] = "stop"
    t_ms: int


HumanInput = Annotated[
    Union[SpecInput, PinInput, ToggleInput, WinnerOverrideInput, StopInput],
    Field(discriminator="kind"),
]


class Credits(WireModel):
    used_usd: float
    cap_usd: float | None = None


# ---------- canvas ops ----------
class AddNodeOp(WireModel):
    op: Literal["add_node"] = "add_node"
    id: str
    kind: NodeKind
    zone: ZoneId
    label: str
    ref_id: str | None = opt()
    near: str | None = opt()


class ConnectOp(WireModel):
    op: Literal["connect"] = "connect"
    source: str
    target: str
    kind: EdgeKind
    label: str | None = opt()


class MoveNodeOp(WireModel):
    op: Literal["move_node"] = "move_node"
    id: str
    x: float
    y: float


class UpdateStatusOp(WireModel):
    op: Literal["update_status"] = "update_status"
    id: str
    status: str
    note: str | None = opt()


class RemoveOp(WireModel):
    op: Literal["remove"] = "remove"
    id: str


CanvasOp = Annotated[
    Union[AddNodeOp, ConnectOp, MoveNodeOp, UpdateStatusOp, RemoveOp],
    Field(discriminator="op"),
]


# ---------- the event stream ----------
class EventBase(WireModel):
    """seq and tMs are stamped by the EventSink (run manager). Producers leave them at 0."""

    seq: int = 0
    t_ms: int = 0


class RunPhaseEvent(EventBase):
    t: Literal["run"] = "run"
    phase: RunPhase
    message: str | None = opt()


class StageEvent(EventBase):
    t: Literal["stage"] = "stage"
    stage: StageId
    status: StageStatus
    detail: str | None = opt()
    round: int | None = opt()


class OpEvent(EventBase):
    t: Literal["op"] = "op"
    by: Actor
    op: CanvasOp


class SpecEvent(EventBase):
    t: Literal["spec"] = "spec"
    spec: Spec


class PlanEvent(EventBase):
    t: Literal["plan"] = "plan"
    revision: int
    blocks: list[PlanBlock]
    diff: PlanDiff | None = opt()
    reason: str | None = opt()


class GateEvent(EventBase):
    t: Literal["gate"] = "gate"
    state: GateState
    auto_continue_ms: int


class CandidateEvent(EventBase):
    t: Literal["candidate"] = "candidate"
    candidate: Candidate


class RoundEvent(EventBase):
    t: Literal["round"] = "round"
    round: int
    parents: list[str]
    elites: list[str] | None = opt()


class WinnerEvent(EventBase):
    t: Literal["winner"] = "winner"
    candidate_id: str
    by: WinnerBy
    ai_pick_id: str


class HumanEvent(EventBase):
    t: Literal["human"] = "human"
    input: HumanInput


class CaptionEvent(EventBase):
    t: Literal["caption"] = "caption"
    text: str
    chapter: str | None = opt()


class CreditsEvent(EventBase):
    t: Literal["credits"] = "credits"
    used_usd: float
    cap_usd: float | None = None


class ArtifactEvent(EventBase):
    t: Literal["artifact"] = "artifact"
    path: str
    content: str


class HumanRefEvent(EventBase):
    """The winner's counterpart: the human-written reference measured with the same tools, plus EQY of the reference
    (gold) against the winner (gate). Emitted once, after the winner and before the terminal run event."""

    t: Literal["human_ref"] = "human_ref"
    ppa: PPA
    eqy: Equivalence


RunEvent = Annotated[
    Union[
        RunPhaseEvent,
        StageEvent,
        OpEvent,
        SpecEvent,
        PlanEvent,
        GateEvent,
        CandidateEvent,
        RoundEvent,
        WinnerEvent,
        HumanEvent,
        CaptionEvent,
        CreditsEvent,
        ArtifactEvent,
        HumanRefEvent,
    ],
    Field(discriminator="t"),
]

TERMINAL_PHASES: frozenset[str] = frozenset({"done", "error", "cap_reached", "stopped"})


# ---------- recorded log ----------
class Chapter(WireModel):
    id: str
    label: str
    t_ms: int


class RunLogMeta(WireModel):
    run_id: str
    sample: bool
    title: str
    preset_id: str
    recorded_at: str  # ISO 8601 UTC, e.g. "2026-10-04T00:00:00Z"
    duration_ms: int
    chapters: list[Chapter] = Field(default_factory=list)
    note: str | None = opt()


class RunLog(WireModel):
    format: Literal["verity.runlog/1"] = "verity.runlog/1"
    meta: RunLogMeta
    events: list[RunEvent]


# ---------- adapters ----------
RUN_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(RunEvent)
CANVAS_OP_ADAPTER: TypeAdapter[Any] = TypeAdapter(CanvasOp)
HUMAN_INPUT_ADAPTER: TypeAdapter[Any] = TypeAdapter(HumanInput)


def parse_event(data: Any) -> Any:
    """dict (camelCase) -> RunEvent model. Raises pydantic.ValidationError."""
    return RUN_EVENT_ADAPTER.validate_python(data)


def parse_event_json(raw: str | bytes) -> Any:
    return RUN_EVENT_ADAPTER.validate_json(raw)


def event_to_wire(event: EventBase) -> dict[str, Any]:
    return to_wire(event)


def event_to_json(event: EventBase) -> str:
    return to_json(event)


def is_terminal(event: EventBase) -> bool:
    return isinstance(event, RunPhaseEvent) and event.phase in TERMINAL_PHASES
