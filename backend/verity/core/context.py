"""RunContext: everything one orchestrator run needs, built by the run manager (builder D).

Entry point (builder D, verity/orchestrator/__init__.py):

    async def run_orchestrator(ctx: RunContext) -> None

Contract:
- Emits `run {phase: "running"}` first and exactly one terminal event: done | error | cap_reached.
- Never emits `stopped`: on Stop the run manager cancels the task; asyncio.CancelledError and RunStopped must
  propagate (never swallow them). The run manager then emits `run {phase: "stopped"}`.
- Sets ctx.outcome before returning normally.
- Calls ctx.beat() at every step and wraps every LLM/tool call in `with ctx.busy():`.
"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import Settings
from ..contracts import Priority
from .errors import RunStopped
from .pack import TaskPack
from .protocols import EventSink, GenerateRtlFn, Ledger, LLMGateway, OptimizeFn, PlanFn, RepairFn, Store, ToolRunner
from .records import PathMode

OutcomeStatus = Literal["done", "error", "cap_reached", "stopped"]
GateStateOrNone = Literal["none", "open", "held", "closed"]


@dataclass(frozen=True, slots=True)
class Roles:
    """The four LLM roles. Injectable so evals and tests can swap in fakes."""

    plan: PlanFn
    generate_rtl: GenerateRtlFn
    repair: RepairFn
    optimize: OptimizeFn


@dataclass(slots=True)
class RunOutcome:
    status: OutcomeStatus
    winner_id: str | None = None
    baseline_id: str | None = None
    error_reason: str | None = None
    versions: int = 0
    fixes: int = 0
    rejected: int = 0
    human_inputs: int = 0


@dataclass(slots=True)
class RunContext:
    run_id: str
    session_id: str
    pack: TaskPack
    priority: Priority
    mode: PathMode
    settings: Settings
    llm: LLMGateway
    roles: Roles
    tools: ToolRunner
    ledger: Ledger
    sink: EventSink
    store: Store
    # contracts.SteerRequest models, put by RunService.steer(), consumed by the orchestrator at the plan gate.
    steer: asyncio.Queue = field(default_factory=asyncio.Queue)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    started_monotonic: float = field(default_factory=time.monotonic)
    last_beat: float = field(default_factory=time.monotonic)
    outcome: RunOutcome | None = None
    # Set by the orchestrator, read by RunService.steer() to answer 409 for steers that can no longer apply.
    gate_state: GateStateOrNone = "none"
    ai_winner_id: str | None = None
    # Research and outcomes (plan claim 2), set by the run manager. `references` is a
    # verity.research.service.RunReferences (technique ids, our own labels, code-written past outcome lines) or None.
    # No web text, no summary text and no user text is ever put here.
    references: Any = None
    outcome_key: str = ""  # sha256 of the family plus its validated knobs (run_outcome.key)
    params_full: dict[str, Any] = field(default_factory=dict)  # validated knobs with defaults filled in
    techniques: dict[str, str] = field(default_factory=dict)  # candidate id -> technique id it was asked to use
    # A CPU core retry only: what the failed job's core checks reported (sandbox and test output, written by code, cleaned
    # by roles.rtl_gen.prior_lines). The first RTL generation reads it (rtl_gen v4); never model or user text.
    prior_attempt: tuple[str, ...] = ()
    # False for a signed-in visitor: the run took no credit, so the orchestrator's credit checkpoints let it pass.
    charge_credits: bool = True
    _busy: int = 0

    # ---------- clock ----------
    def now_ms(self) -> int:
        """Milliseconds since run start. Use for HumanInput.tMs; the sink stamps event tMs itself."""
        return int((time.monotonic() - self.started_monotonic) * 1000)

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    # ---------- liveness (ARCHITECTURE 18.3) ----------
    def beat(self) -> None:
        self.last_beat = time.monotonic()

    @contextmanager
    def busy(self) -> Iterator[None]:
        """Wrap every LLM and tool call. While busy, the run manager's ticker keeps heartbeat_at fresh."""
        self._busy += 1
        self.beat()
        try:
            yield
        finally:
            self._busy -= 1
            self.beat()

    @property
    def is_busy(self) -> bool:
        return self._busy > 0

    def is_alive(self, stall_s: float) -> bool:
        return self.is_busy or (time.monotonic() - self.last_beat) < stall_s

    # ---------- stop ----------
    @property
    def stopped(self) -> bool:
        return self.stop.is_set()

    def check_stop(self) -> None:
        if self.stop.is_set():
            raise RunStopped()
