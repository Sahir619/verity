"""Mapping from orchestrator state to RunEvents (CONTRACTS section 6). Every event is a verity.contracts model.

`Emitter` is a thin typed layer over `ctx.sink.emit(run_id, event)`. It never stamps `seq` or `tMs` (the sink does)
and never builds JSON (the API layer serializes with `event_to_json`).

Canvas ids, zones and edges follow frontend/public/runs/golden.json:
  spec -> `spec` (zone plan), plan blocks -> `blk-<slug>` (plan), golden model -> `golden` (plan),
  candidate n -> `c<n>` labeled `v<n>` (tree). `add_node` and `connect` use by "ai", `update_status` uses "system".
"""

import json

from ..contracts import (
    AddNodeOp,
    ArtifactEvent,
    CandidateEvent,
    CaptionEvent,
    ConnectOp,
    CreditsEvent,
    EdgeKind,
    EventBase,
    GateEvent,
    GateState,
    HumanEvent,
    HumanInput,
    HumanRefEvent,
    NodeKind,
    PPA,
    OpEvent,
    PlanBlock,
    PlanDiff,
    PlanEvent,
    RemoveOp,
    RoundEvent,
    RunPhase,
    RunPhaseEvent,
    Equivalence,
    Spec,
    SpecEvent,
    StageEvent,
    StageId,
    StageStatus,
    UpdateStatusOp,
    WinnerBy,
    WinnerEvent,
    ZoneId,
)
from ..core.types import RunContext
from .candidates import Cand

TRUNCATION_MARKER = "\n[truncated]"


def _json_size(text: str) -> int:
    """Bytes the text takes inside a JSON string (escapes counted), without the two quotes."""
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2


def cap_content(text: str, max_event_bytes: int) -> str:
    """Artifact content cap: content at most `max_event_bytes - 1024` bytes as it appears on the wire, with
    '\\n[truncated]' appended when it was cut (CONTRACTS section 6.2)."""
    limit = max(1024, max_event_bytes - 1024)
    if _json_size(text) <= limit:
        return text
    cut = max(0, int(len(text) * (limit - len(TRUNCATION_MARKER)) / max(1, _json_size(text))))
    while cut > 0 and _json_size(text[:cut] + TRUNCATION_MARKER) > limit:
        cut = int(cut * 0.9)
    return text[:cut] + TRUNCATION_MARKER


class Emitter:
    """Typed event helpers for one run. Also remembers the current caption chapter and the human-input count."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.chapter: str | None = None
        self.human_inputs = 0

    async def emit(self, event: EventBase) -> EventBase:
        return await self.ctx.sink.emit(self.ctx.run_id, event)

    # ---------- run, stage, text ----------
    async def run(self, phase: RunPhase, message: str | None = None) -> None:
        await self.emit(RunPhaseEvent(phase=phase, message=message))

    async def stage(
        self, stage: StageId, status: StageStatus, detail: str | None = None, round_n: int | None = None
    ) -> None:
        await self.emit(StageEvent(stage=stage, status=status, detail=detail, round=round_n))

    async def caption(self, text: str, chapter: str | None = None) -> None:
        """A caption from core/captions.py. `chapter` None keeps the current chapter."""
        if chapter is not None:
            self.chapter = chapter
        await self.emit(CaptionEvent(text=text, chapter=self.chapter))

    async def human(self, human_input: HumanInput) -> None:
        self.human_inputs += 1
        await self.emit(HumanEvent(input=human_input))

    # ---------- spec, plan, gate ----------
    async def spec(self, spec: Spec) -> None:
        await self.emit(SpecEvent(spec=spec))

    async def plan(
        self, revision: int, blocks: list[PlanBlock], diff: PlanDiff | None = None, reason: str | None = None
    ) -> None:
        await self.emit(PlanEvent(revision=revision, blocks=blocks, diff=diff, reason=reason))

    async def gate(self, state: GateState, auto_continue_ms: int) -> None:
        self.ctx.gate_state = state
        await self.emit(GateEvent(state=state, auto_continue_ms=max(0, int(auto_continue_ms))))

    # ---------- canvas ----------
    async def add_node(
        self, node_id: str, kind: NodeKind, zone: ZoneId, label: str, *, ref_id: str | None = None
    ) -> None:
        op = AddNodeOp(id=node_id, kind=kind, zone=zone, label=label, ref_id=ref_id or node_id)
        await self.emit(OpEvent(by="ai", op=op))

    async def connect(self, source: str, target: str, kind: EdgeKind, label: str | None = None) -> None:
        await self.emit(OpEvent(by="ai", op=ConnectOp(source=source, target=target, kind=kind, label=label)))

    async def remove(self, node_id: str) -> None:
        await self.emit(OpEvent(by="ai", op=RemoveOp(id=node_id)))

    async def update_status(self, node_id: str, status: str, note: str | None = None) -> None:
        await self.emit(OpEvent(by="system", op=UpdateStatusOp(id=node_id, status=status, note=note)))

    # ---------- candidates and rounds ----------
    async def candidate(self, cand: Cand) -> None:
        """Full upsert: event first, then the store row (the event stream is the source of truth)."""
        model = cand.to_model()
        await self.emit(CandidateEvent(candidate=model))
        await self.ctx.store.upsert_candidate(self.ctx.run_id, model)

    async def round(self, round_n: int, parents: list[str], elites: list[str] | None = None) -> None:
        await self.emit(RoundEvent(round=round_n, parents=parents, elites=elites))

    async def winner(self, candidate_id: str, by: WinnerBy, ai_pick_id: str) -> None:
        await self.emit(WinnerEvent(candidate_id=candidate_id, by=by, ai_pick_id=ai_pick_id))

    async def human_ref(self, ppa: PPA, eqy: Equivalence) -> None:
        await self.emit(HumanRefEvent(ppa=ppa, eqy=eqy))

    # ---------- spend and files ----------
    async def credits(self) -> None:
        snap = self.ctx.ledger.snapshot()
        await self.emit(CreditsEvent(used_usd=round(snap.usd, 4), cap_usd=self.ctx.ledger.limits.max_usd))

    async def artifact(self, path: str, content: str) -> None:
        capped = cap_content(content, self.ctx.settings.sse.max_event_bytes)
        await self.emit(ArtifactEvent(path=path, content=capped))
