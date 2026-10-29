"""Interfaces between builders. Implementations must match these signatures exactly.

Owner of each implementation:
  LLMProvider, LLMGateway, role functions ........ builder B (verity/llm, verity/roles)
  ToolRunner ...................................... builder C (verity/tools)
  Ledger, EventSink, RunService, run_orchestrator . builder D (verity/runs, verity/orchestrator)
  Store ........................................... builder E (verity/store)
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..config import RoleName
from ..contracts import (
    PPA,
    AskReply,
    Attempt,
    Candidate,
    EventBase,
    HealthResponse,
    MyRunsResponse,
    ParseReply,
    ThreadMessageReply,
    ThreadsResponse,
    ThreadView,
    PinInput,
    Priority,
    RunLog,
    RunSummaryItem,
    RunsResponse,
    ToggleInput,
    Verification,
)
from .budget import BudgetLimits, CountKind, Reservation, SpendSnapshot, Usage
from .llm_types import LLMRequest, LLMResponse, PlanOut, PlanResult, ResponseFormat, RtlOut, TriedVersion
from .pack import TaskPack
from .records import (
    ArtifactRef,
    AttemptMeta,
    CandidateRow,
    EvalResultRow,
    PathMode,
    ResearchRecord,
    RunOutcomeRow,
    RunPatch,
    RunRecord,
    RunStatus,
    StrategyOutcomeRow,
    ThreadData,
    ThreadListItem,
    ThreadTurnIn,
    UserRunRow,
)
from .mutants import Mutant
from .results import (
    EquivalenceResult,
    GoldenCheckResult,
    MeasureResult,
    SeedsResult,
    StrengthResult,
    VerifyResult,
    WaveResult,
    WaveTrace,
)
from .rtl import Rtl

# ======================================================================
# LLM (builder B)
# ======================================================================


@runtime_checkable
class LLMProvider(Protocol):
    """The only code that talks to a model API. NebiusProvider / OpenAICompatProvider / FakeProvider."""

    name: str  # "nebius" | "openai_compat" | "fake"

    async def complete(self, req: LLMRequest) -> LLMResponse:
        """One request. Raises LLMError(retryable=True) on 429/5xx/timeouts, LLMError(retryable=False) otherwise.
        Does NOT retry and does NOT touch the ledger (the gateway does both)."""
        ...

    async def aclose(self) -> None: ...


class LLMGateway(Protocol):
    """Per-run role-aware wrapper: route -> model, ledger reserve/commit, call caps, retries with backoff.

    Implementation: verity.llm.gateway.RunLLM(provider, settings, ledger). One instance per run.
    """

    async def call(
        self,
        role: RoleName,
        *,
        system: str,
        user: str,
        response_format: ResponseFormat = "text",
        prompt_version: str = "",
        tags: Mapping[str, str] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> tuple[LLMResponse, float]:
        """Returns (response, estimated usd). Before the call: estimate prompt tokens, raise LLMError if above
        budget.call.max_prompt_tokens, then ledger.reserve() (raises BudgetExhausted). After: ledger.commit().
        On failure: ledger.release(), retry retryable errors up to llm.retries with backoff, then raise LLMError."""
        ...


# ---------- role functions (verity.roles.planner.plan, rtl_gen.generate_rtl, repair.repair, optimizer.optimize) ----------
class PlanFn(Protocol):
    async def __call__(
        self,
        llm: LLMGateway,
        pack: TaskPack,
        priority: Priority,
        *,
        previous: PlanOut | None = None,
        steering: Sequence[PinInput | ToggleInput] = (),
        references: str = "",
    ) -> PlanResult:
        """Revision 1 when previous is None; otherwise a replan honoring the pins/toggles.
        `references`: the code-built "Known approaches" section (only passed when there is one).
        Retries once on invalid JSON (plan.json_retries), then raises RoleOutputError."""
        ...


class GenerateRtlFn(Protocol):
    async def __call__(
        self,
        llm: LLMGateway,
        pack: TaskPack,
        plan: PlanOut,
        priority: Priority,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        prior_attempt: Sequence[str] = (),
    ) -> RtlOut:
        """Baseline RTL (createdBy INIT). Raises RoleOutputError if no Verilog block can be extracted.
        temperature and top_p override the role route for this call (R4 baseline sampling). prior_attempt: what an
        earlier attempt at this same design failed (code-written lines, a CPU core retry); the orchestrator passes it
        only when there is some."""
        ...


class RepairFn(Protocol):
    async def __call__(
        self,
        llm: LLMGateway,
        pack: TaskPack,
        rtl_text: str,
        verification: Verification,
        *,
        attempt_n: int,
        waves: WaveTrace | None = None,
        parent_rtl: str | None = None,
    ) -> RtlOut:
        """FIX. Input: the failing RTL text (may be RTL that was rejected before sim) and its Verification
        (lint log or up to 5 failing cases). `waves`: the first failing case cycle by cycle around the mismatch
        (/verity/bin/wave), when there is one. `parent_rtl`: the passing parent of a failing child (never for v1),
        shown in the prompt so the fix can restore the parent's logic. Returns new RTL plus `diagnosis`."""
        ...


class OptimizeFn(Protocol):
    async def __call__(
        self,
        llm: LLMGateway,
        pack: TaskPack,
        parent_rtl: str,
        parent_ppa: PPA,
        baseline_ppa: PPA,
        priority: Priority,
        strategy: str,
        *,
        idea: str | None = None,
        parent_measure: MeasureResult | None = None,
        tried: Sequence[TriedVersion] = (),
    ) -> RtlOut:
        """SIMPLIFY | OPTIMIZE | EXPLORE child. Must keep the interface and latency. `idea` (EXPLORE only, passed
        only when set): one technique from the fixed list, our own text, replacing the generic EXPLORE idea.
        `parent_measure`: the parent's synthesis and timing result, for the evidence block (cell types by area, flip-flop
        count, critical path). `tried`: the other finished versions of this run (strategy, reason, result vs v1 or
        rejected), listed in the prompt so the child does something different."""
        ...


# ======================================================================
# Tools (builder C)
# ======================================================================


@runtime_checkable
class ToolRunner(Protocol):
    """The only code that executes anything. Fixed command templates; RTL travels as a file.

    Implementations: verity.tools.local_docker.LocalDockerRunner, verity.tools.nebius_sandbox.NebiusSandboxRunner.
    Timeouts become results (status "timeout"); infrastructure failures raise ToolInfraError after
    tools.infra_retries retries. A failing design is never an exception.
    """

    async def verify(self, rtl: Rtl, pack: TaskPack) -> VerifyResult:
        """/verity/bin/verify: interface check, Verilator lint, Icarus + cocotb sim (early exit)."""
        ...

    async def measure(self, rtl: Rtl, pack: TaskPack) -> MeasureResult:
        """/verity/bin/measure: Yosys synth (Nangate45) + OpenSTA + CircuitGraph (candidateId left empty)."""
        ...

    async def check_equivalence(self, gold: Rtl, gate: Rtl, pack: TaskPack) -> EquivalenceResult:
        """/verity/bin/eqy: gold = v1 (mounted as /work/dut.v), gate = candidate (/work/b.v)."""
        ...

    async def verify_seeds(self, rtl: Rtl, pack: TaskPack, seeds: Sequence[int]) -> SeedsResult:
        """/verity/bin/seeds: Verilator lint (warnings by tag), then the locked tests once per seed. Sign-off."""
        ...

    async def test_strength(self, rtl: Rtl, pack: TaskPack, mutants: Sequence[Mutant]) -> StrengthResult:
        """/verity/bin/strength: the locked tests on every planted mutant (sent as /work/mutants.json). Sign-off."""
        ...

    async def waveform(self, rtl: Rtl, pack: TaskPack, case: str | None = None) -> WaveResult:
        """/verity/bin/wave: one case (None: the first) simulated with a VCD dump, cut around its first mismatch."""
        ...

    async def check_golden(self, golden_py: str, cases: Sequence[dict]) -> GoldenCheckResult:
        """/verity/bin/golden_check (CE3): pure-Python self-check of a drafted golden model, before any Verilog
        exists. No pack mount. `cases`: [{"name", "steps": [{"inputs", "rst", "expected"}, ...]}, ...]."""
        ...

    async def image_digest(self) -> str:
        """Digest of the tool image, recorded on every run. "" if unknown."""
        ...

    async def sandbox_problem(self) -> str | None:
        """A plain sentence when the sandbox cannot run anything now (Docker down, image missing), else None.
        Fast and cached (about 15 s); GET /health reports `sandbox_down` and POST /runs answers 503 when it is set."""
        ...


# ======================================================================
# Budget ledger (builder D)
# ======================================================================


class Ledger(Protocol):
    """Per-run, in-memory, synchronous. Every role asks it before spending (ARCHITECTURE 20)."""

    limits: BudgetLimits

    def reserve(self, *, role: str, model: str, max_input_tokens: int, max_output_tokens: int) -> Reservation:
        """Raises BudgetExhausted if the worst case (current + all open reservations + this one) would cross
        max_llm_calls, max_input_tokens, max_output_tokens or max_usd, or if wall time is over max_wall_s."""
        ...

    def commit(self, reservation: Reservation, usage: Usage) -> None: ...

    def release(self, reservation: Reservation) -> None: ...

    def can_count(self, kind: CountKind) -> bool:
        """candidate: total < max_candidates; round: total < max_rounds; repair: always True here.
        The per-candidate repair limit is the orchestrator's check:
        candidate.fix_count < (limits.max_repairs_baseline if baseline else limits.max_repairs_child)."""
        ...

    def count(self, kind: CountKind) -> None:
        """Increment the total for kind (snapshot().candidates / repairs / rounds)."""
        ...

    def exhausted(self) -> str | None:
        """Name of the first limit that is used up (BudgetLimits field name), or None."""
        ...

    def snapshot(self) -> SpendSnapshot: ...


# ======================================================================
# Events (builder D implements, orchestrator and run manager call)
# ======================================================================


class EventSink(Protocol):
    """Stamps seq (gap-free per run, from 1) and tMs (ms since run start, non-decreasing), writes the event
    to the Store FIRST, then fans it out to live subscribers. Returns the stamped event.
    On CreditsEvent it also persists runs.spent_json and adds the delta to the daily spend."""

    async def emit(self, run_id: str, event: EventBase) -> EventBase: ...


# ======================================================================
# Store (builder E)
# ======================================================================


class Store(Protocol):
    """SQLite (aiosqlite) + write-once artifact directory under settings.data_dir (ARCHITECTURE 7, 15)."""

    async def open(self) -> None:
        """Create tables if missing (idempotent)."""
        ...

    async def close(self) -> None: ...

    # ---- runs ----
    async def create_run(self, run: RunRecord) -> None: ...

    async def get_run(self, run_id: str) -> RunRecord | None: ...

    async def update_run(self, run_id: str, patch: RunPatch) -> None:
        """Writes only patch.model_fields_set."""
        ...

    async def touch_heartbeat(self, run_id: str, at: datetime) -> None: ...

    async def runs_with_status(self, statuses: Sequence[RunStatus]) -> list[RunRecord]: ...

    async def count_runs(
        self,
        *,
        statuses: Sequence[RunStatus] | None = None,
        session_id: str | None = None,
        created_since: datetime | None = None,
    ) -> int: ...

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummaryItem]: ...

    # ---- candidates and attempts ----
    async def upsert_candidate(self, run_id: str, candidate: Candidate) -> None:
        """Full upsert keyed by (run_id, candidate.id). Stores ppa/fitness/eqy JSON and the flags."""
        ...

    async def add_attempt(self, run_id: str, candidate_id: str, attempt: Attempt, meta: AttemptMeta) -> None:
        """Insert keyed by (run_id, candidate_id, attempt.n). Re-adding the same n replaces the verification
        (used when a diagnosis is attached after the fact)."""
        ...

    # ---- events ----
    async def append_event(self, run_id: str, event: EventBase) -> None:
        """event is already stamped. Raises if (run_id, seq) exists (append-only)."""
        ...

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[EventBase]:
        """Events with seq > after_seq, ordered by seq, parsed back into RunEvent models."""
        ...

    async def last_seq(self, run_id: str) -> int: ...

    # ---- artifacts (write-once) ----
    async def put_blob(self, data: bytes, mime: str) -> ArtifactRef:
        """data/blobs/<sha[0:2]>/<sha>. Idempotent for identical content."""
        ...

    async def write_run_file(self, run_id: str, relpath: str, data: bytes, mime: str, *, overwrite: bool = False) -> ArtifactRef:
        """data/runs/<run_id>/<relpath>. relpath must match ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ with no '..'.
        Writing an existing path with different content raises FileExistsError unless overwrite is True."""
        ...

    async def read_run_file(self, run_id: str, relpath: str) -> bytes | None: ...

    # ---- evals and spend ----
    async def add_eval_result(self, row: EvalResultRow) -> None: ...

    async def add_daily_spend(self, day: str, usd: float, session_id: str | None = None) -> None:
        """day = UTC 'YYYY-MM-DD'. Adds (not sets). With a session id, the same amount is added to that session's
        day in the same transaction (migration 5), so the two totals cannot drift."""
        ...

    async def daily_spend(self, day: str) -> float: ...

    async def session_spend(self, session_id: str, day: str) -> float:
        """What one session (later one user) has spent on this UTC day, from add_daily_spend(..., session_id)."""
        ...

    async def add_serper_credits(self, day: str, credits: int) -> None:
        """Daily Serper credit counter (daily_spend.serper_credits). Adds (not sets)."""
        ...

    async def serper_credits(self, day: str) -> int: ...

    async def add_tavily_credits(self, day: str, credits: int) -> None:
        """Daily Tavily credit counter (daily_spend.tavily_credits, migration 4). Adds (not sets)."""
        ...

    async def tavily_credits(self, day: str) -> int: ...

    # ---- candidates read back (outcome writer) ----
    async def candidates_for_run(self, run_id: str) -> list[CandidateRow]: ...

    # ---- research (migration 3) ----
    async def put_research(self, record: ResearchRecord) -> None: ...

    async def get_research(self, research_id: str) -> ResearchRecord | None: ...

    async def research_by_key(self, key: str, now: datetime) -> ResearchRecord | None:
        """Newest fresh research with status ok for this family-plus-knobs key."""
        ...

    async def research_by_query_key(self, query_key: str, now: datetime) -> ResearchRecord | None: ...

    async def research_for_family(self, family: str, now: datetime, limit: int = 50) -> list[ResearchRecord]: ...

    async def grant_research(self, research_id: str, session_hmac: str) -> None: ...

    async def research_granted(self, research_id: str, session_hmac: str) -> bool: ...

    # ---- run outcomes (migration 3) ----
    async def put_run_outcome(self, row: RunOutcomeRow) -> None:
        """Idempotent upsert keyed by run_id."""
        ...

    async def put_strategy_outcomes(self, run_id: str, rows: Sequence[StrategyOutcomeRow]) -> None: ...

    async def get_run_outcome(self, run_id: str) -> RunOutcomeRow | None: ...

    async def run_outcomes(
        self, key: str, pack_sha256: str, tool_image_digest: str, limit: int = 20
    ) -> list[RunOutcomeRow]: ...

    async def strategy_outcomes(
        self, key: str, pack_sha256: str, tool_image_digest: str, limit: int = 200
    ) -> list[StrategyOutcomeRow]: ...


# ======================================================================
# Threads (the bench chat after the first chip request)
# ======================================================================


@runtime_checkable
class ThreadStore(Protocol):
    """Every read and write of a thread, and nothing else, so the chats can move to another database without touching
    the agent or the routes. SqliteStore implements it (migrations 6 and 7) and so does SupabaseThreadStore (Postgres
    through the Data API; a store that cannot be reached raises core.errors.StoreUnavailable, HTTP 502). `owner` is
    core.budget.spend_owner(session id)."""

    async def get_or_create(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData:
        """The thread, created empty (no facts, no summary, no turns) when this owner does not have it."""
        ...

    async def load(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData | None:
        """Facts, summary, summarized_through and the turns; None for a thread this owner does not have. With
        `since_summary` only the rows after `summarized_through` come back (what the agent reads); otherwise all."""
        ...

    async def append_turns(self, owner: str, thread_id: str, turns: Sequence[ThreadTurnIn]) -> int:
        """Add messages in order, in one transaction, cut to THREAD_USER_MAX and THREAD_VERITY_MAX characters. Returns
        the seq of the last row. Raises NotFound for a thread this owner does not have."""
        ...

    async def update_facts(self, owner: str, thread_id: str, facts: dict[str, Any]) -> None:
        """Replace the facts record (written by code only). Raises NotFound."""
        ...

    async def update_summary(self, owner: str, thread_id: str, summary: str, through_seq: int) -> None:
        """Replace the summary; every row up to `through_seq` is folded into it. Raises NotFound."""
        ...

    async def list_threads(self, owner: str, limit: int = 50) -> list[ThreadListItem]:
        """This owner's threads that have at least one message, newest first."""
        ...

    async def claim(self, from_owner: str, to_owner: str) -> int:
        """Move every thread of one owner to another (a sign-in claiming what the session made). Returns how many."""
        ...

    async def add_user_run(self, owner: str, run_id: str, thread_id: str | None, title: str) -> None:
        """Record that `owner` started `run_id` (in `thread_id` when it came from a thread). Idempotent per run."""
        ...

    async def list_user_runs(self, owner: str, limit: int = 50) -> list[UserRunRow]:
        """The runs this owner started, newest first."""
        ...

    # ---- user credits ----
    async def get_credits(self, owner: str, default: int = 3) -> int:
        """Remaining run credits for this owner (auto-seeded with default if not found)."""
        ...

    async def deduct_credit(self, owner: str, default: int = 3) -> int:
        """Deduct 1 credit for this owner. Returns remaining credits. Returns -1 if 0 credits."""
        ...

    async def set_credits(self, owner: str, credits: int, email: str | None = None, role: str | None = None) -> None:
        """Set credits for this owner."""
        ...

    async def list_user_credits(self) -> list[dict[str, Any]]:
        """List all user credits with usage info."""
        ...


# ======================================================================
# Run service: what the API layer (E) calls on the run manager (D)
# ======================================================================


class RunService(Protocol):
    """Implementation: verity.runs.manager.RunManager(settings, store, provider, tools, packs)."""

    async def start(self) -> None:
        """Startup: mark queued/running runs as error 'server restarted' (emitting a run error event),
        start the watchdog task."""
        ...

    async def shutdown(self) -> None:
        """Cancel live runs (they end as stopped/error), stop the watchdog."""
        ...

    def presets(self) -> Mapping[str, TaskPack]:
        """Live presets only (eval_only = false), keyed by id (= web presetId)."""
        ...

    async def create_run(
        self,
        *,
        session_id: str,
        preset_id: str,
        priority: Priority,
        mode: PathMode,
        params: Mapping[str, object] | None = None,
        research_id: str | None = None,
        thread_id: str | None = None,
        charge_credits: bool = True,
    ) -> RunRecord:
        """Admission (concurrency per session and global, daily spend), create the run row, start the asyncio
        task. `charge_credits` False (a signed-in visitor) skips the credit check and the credit taken. Raises UnknownPreset, AdmissionDenied(reason), ParamsError (bad knob values). A research_id that this
        session was not handed, or that does not match the family and knobs, is ignored (never an error). A thread_id
        that is this session's thread gets the run id in its facts; any other is ignored."""
        ...

    async def steer(self, run_id: str, session_id: str, request: object) -> None:
        """request: a contracts.SteerRequest model. Raises NotFound, Forbidden, Conflict. The echo `human`
        event is emitted by the orchestrator when it applies the steer, not here."""
        ...

    async def stop(self, run_id: str, session_id: str) -> None:
        """Sets the stop flag and cancels the run task. Raises NotFound, Forbidden. Idempotent."""
        ...

    def stream(self, run_id: str, after_seq: int = 0) -> AsyncIterator[EventBase]:
        """Stored events with seq > after_seq, then live events, with no gap and no duplicate. Ends after the
        terminal `run` event (done | error | cap_reached | stopped) has been yielded."""
        ...

    async def get_run(self, run_id: str) -> RunRecord | None: ...

    async def run_log(self, run_id: str) -> RunLog:
        """RunLog built from stored events (same format as golden.json, meta.sample = false)."""
        ...

    async def health(self) -> HealthResponse: ...

    async def parse(self, text: str, *, session_id: str = "") -> ParseReply:
        """POST /parse (fact check): one parser-route call, validated against the knob tables, then research for
        every class but not_chip. Raises AdmissionDenied (maintenance | budget_exhausted) and LLMError (the parser
        call itself failed). Worst-case spend is reserved on the daily budget before the calls; the real cost is
        added to the daily spend."""
        ...

    async def list_runs(self, limit: int = 50, offset: int = 0) -> RunsResponse:
        """GET /runs: list recent runs with their summary and winner PPA."""
        ...

    async def ask(self, run_id: str, *, session_id: str, text: str, focus_version: str | None = None) -> AskReply:
        """POST /runs/{id}/ask: one chat turn about an ENDED run from its owner session. Raises NotFound, Forbidden,
        Conflict (queued or running) and AdmissionDenied (maintenance | budget_exhausted, only for a turn that needs a
        model call). A model failure never raises: the turn falls back to a code answer."""
        ...

    async def thread_message(
        self,
        thread_id: str,
        *,
        session_id: str,
        text: str,
        run_id: str | None = None,
        focus_version: str | None = None,
        cpu_job_id: str | None = None,
        can_build_cpu: bool = False,
        cpu_admin: bool = False,
    ) -> ThreadMessageReply:
        """POST /threads/{id}/messages: one message of a thread. The first chip request runs the fact check (parse) in
        the same call; every message then goes to the agent (verity.ask.agent). `run_id` and `cpu_job_id` (the CPU job
        on screen) were already checked to be this session's (or an admin's to read) by the route. `can_build_cpu` is the
        route's settings.can_build for this user (signed in; the agent's CPU cards and facts follow it) and `cpu_admin` its
        settings.is_admin (only an admin may name another account's CPU job). Raises AdmissionDenied (maintenance | budget_exhausted | session_budget_exhausted) and
        LLMError (the fact check's parser call failed). A failed agent call never raises: the reply falls back to code."""
        ...

    async def list_threads(self, session_id: str, limit: int = 50) -> ThreadsResponse:
        """GET /threads: this session's threads, newest first."""
        ...

    async def read_thread(self, session_id: str, thread_id: str) -> ThreadView:
        """GET /threads/{id}: the stored turns of one of this session's threads. Raises NotFound."""
        ...

    async def list_my_runs(self, session_id: str, limit: int = 50) -> MyRunsResponse:
        """GET /runs/mine: the runs this session started (a signed-in user's, on any device), newest first."""
        ...

    async def resume_run(self, run_id: str, *, session_id: str, charge_credits: bool = True) -> RunRecord:
        """POST /runs/{id}/resume: Resume an active run that stopped at a checkpoint (e.g. credits exhausted)."""
        ...

    async def get_credits(self, session_id: str) -> int:
        """Remaining run credits for this session (owner)."""
        ...

    async def set_credits(self, session_id: str, credits: int, email: str | None = None, role: str | None = None) -> None:
        """Set run credits for this session (owner)."""
        ...

    async def list_user_credits(self) -> list[dict[str, Any]]:
        """List all user credits and usage info for admin review."""
        ...


__all__ = [
    "LLMProvider",
    "LLMGateway",
    "PlanFn",
    "GenerateRtlFn",
    "RepairFn",
    "OptimizeFn",
    "ToolRunner",
    "Ledger",
    "EventSink",
    "Store",
    "ThreadStore",
    "RunService",
    "EvalResultRow",
    "ArtifactRef",
    "AttemptMeta",
    "RunRecord",
    "RunPatch",
]
