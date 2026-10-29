"""Store records (ARCHITECTURE 7). Backend-only; snake_case JSON in SQLite."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..contracts import Priority
from .budget import BudgetLimits, SpendSnapshot

RunStatus = Literal["queued", "running", "done", "error", "cap_reached", "stopped", "timed_out"]
ACTIVE_STATUSES: tuple[RunStatus, ...] = ("queued", "running")
PathMode = Literal["fast", "full"]


class RunRecord(BaseModel):
    id: str  # "r_<uuid4 hex>"
    session_id: str
    preset_id: str
    priority: Priority
    mode: PathMode
    status: RunStatus = "queued"
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    heartbeat_at: datetime | None = None
    budget: BudgetLimits
    spent: SpendSnapshot = Field(default_factory=SpendSnapshot)
    task_pack_sha256: str
    tool_image_digest: str = ""
    models: dict[str, str] = Field(default_factory=dict)  # role -> model id
    prompt_versions: dict[str, str] = Field(default_factory=dict)  # role -> prompt version
    error_reason: str | None = None
    winner_id: str | None = None
    git_sha: str | None = None
    params: dict[str, Any] | None = None
    title: str | None = None
    research_id: str | None = None  # the research the run used (POST /runs researchId, or found by key)


class RunPatch(BaseModel):
    """Partial update. Store.update_run writes only the fields that were set (model_fields_set)."""

    status: RunStatus | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    heartbeat_at: datetime | None = None
    spent: SpendSnapshot | None = None
    tool_image_digest: str | None = None
    models: dict[str, str] | None = None
    prompt_versions: dict[str, str] | None = None
    error_reason: str | None = None
    winner_id: str | None = None
    params: dict[str, Any] | None = None
    title: str | None = None


class AttemptMeta(BaseModel):
    """Backend-only attempt fields (ARCHITECTURE 6). All optional: a FIX that never reached the LLM has none."""

    model_id: str | None = None
    prompt_version: str | None = None
    llm_request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    rtl_sha256: str | None = None
    rtl_artifact: str | None = None  # run-relative path, e.g. candidates/c1/attempt-1.v
    tool_image_digest: str | None = None
    created_at: datetime


class ArtifactRef(BaseModel):
    sha256: str
    path: str  # absolute path of the file under data/
    size_bytes: int
    mime: str


class EvalResultRow(BaseModel):
    eval_run_id: str
    task_id: str
    suite: str
    metrics: dict[str, Any]
    git_sha: str | None = None
    created_at: datetime


# ---------- research and outcomes (migration 3, plan claim 2) ----------
class ResearchSourceRow(BaseModel):
    """One allowlisted search result kept for a research record (research_source joined with source)."""

    n: int  # the S number shown to the user and to the summary model
    url: str
    domain: str
    tier: Literal["A", "B", "C", "P"]
    title: str
    endpoint: Literal["search", "scholar", "patents"]
    query_idx: int = 0
    rank: int = 0  # position Serper gave it
    snippet: str = ""  # 300 characters or fewer, sanitized
    date: str | None = None
    score: float = 0.0
    relevance: float = 0.0
    cited: bool = False


class ResearchRecord(BaseModel):
    """One fact-check research. Shared between sessions by key, so it never holds the user's sentence (only its
    sha256) and the queries are code templates, never raw user text."""

    id: str  # "rs_<uuid4 hex>"
    created_at: datetime
    expires_at: datetime
    key: str | None = None  # sha256 of family plus canonical knobs (in-family requests only)
    query_key: str  # sha256 of the templated queries
    family: str | None = None
    params: dict[str, Any] | None = None
    intent_class: str
    intent_label: str  # the code-filtered label (hardware vocabulary only)
    text_sha256: str
    verdict: str
    status: Literal["ok", "partial", "failed"]
    queries: list[dict[str, str]] = Field(default_factory=list)  # [{"endpoint": "search", "q": "..."}]
    sentences: list[dict[str, Any]] = Field(default_factory=list)  # [{"text": "...", "sourceIds": [1, 2]}]
    techniques: list[str] = Field(default_factory=list)  # ids from verity.research.techniques only
    done_before: bool = False
    model: str | None = None  # served model id of the summary call
    prompt_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_usd: float = 0.0
    serper_credits: int = 0
    tavily_credits: int = 0
    usd: float = 0.0  # llm_usd plus search credits (Serper at research.usd_per_credit, Tavily at tavily_usd_per_credit)
    latency_ms: int = 0
    sources: list[ResearchSourceRow] = Field(default_factory=list)


class CandidateRow(BaseModel):
    """What the outcome writer needs from a stored candidate."""

    id: str
    label: str
    round: int
    parent_id: str | None = None
    created_by: str
    status: str
    fitness: float | None = None
    area_um2: float | None = None
    clock_period_ns: float | None = None
    power_mw: float | None = None
    eqy: str | None = None
    fix_count: int = 0
    is_baseline: bool = False


class RunOutcomeRow(BaseModel):
    """One row per run, upserted from both endings (orchestrator summary and run manager finalize).
    The *_rel numbers are ratios against the run's own v1 (lower is better for all three)."""

    run_id: str
    research_id: str | None = None
    family: str
    params: dict[str, Any] = Field(default_factory=dict)
    key: str
    priority: str
    mode: str
    status: str
    partial: bool
    pack_sha256: str
    tool_image_digest: str
    models: dict[str, str] = Field(default_factory=dict)
    v1_area_um2: float | None = None
    v1_clock_ns: float | None = None
    v1_power_mw: float | None = None
    winner_id: str | None = None
    winner_label: str | None = None
    winner_strategy: str | None = None
    winner_technique: str | None = None
    winner_area_rel: float | None = None
    winner_clock_rel: float | None = None
    winner_power_rel: float | None = None
    winner_eqy: str | None = None
    versions: int = 0
    fixes: int = 0
    rejected: int = 0
    usd: float = 0.0
    llm_calls: int = 0
    wall_s: float = 0.0
    created_at: datetime


class StrategyOutcomeRow(BaseModel):
    """One row per version a run tried after v1."""

    run_id: str
    candidate_id: str
    label: str
    key: str
    pack_sha256: str
    tool_image_digest: str
    strategy: str  # SIMPLIFY | OPTIMIZE | EXPLORE
    technique: str | None = None
    result: Literal["improved", "not_better", "rejected", "not_equivalent", "unfinished"]
    area_rel: float | None = None
    clock_rel: float | None = None
    power_rel: float | None = None


# What a thread keeps (migration 6; core.protocols.ThreadStore). The user's words are capped when stored, so a thread
# never grows without bound.
THREAD_USER_MAX = 300
THREAD_VERITY_MAX = 1_200
THREAD_TITLE_MAX = 60


def thread_title(first_user_text: str) -> str:
    """The title of a thread in the list: its first user message, on one line, cut to THREAD_TITLE_MAX characters."""
    return " ".join(first_user_text.split())[:THREAD_TITLE_MAX]


# The wire reply of a Verity turn ({"check": ParseReply | null, "reply": AgentReply | null}, camelCase JSON) is kept so a
# thread can be drawn again on another device. Larger than this, it is dropped and only the text and steps remain.
THREAD_REPLY_MAX_BYTES = 32 * 1024


def capped_reply(reply: dict[str, Any] | None) -> dict[str, Any] | None:
    """`reply` when its JSON fits THREAD_REPLY_MAX_BYTES, else None (dropped)."""
    if not reply:
        return None
    size = len(json.dumps(reply, separators=(",", ":"), default=str).encode("utf-8"))
    return reply if size <= THREAD_REPLY_MAX_BYTES else None


class ThreadTurnIn(BaseModel):
    """One message to append to a thread: the user's, or Verity's with the tool steps it took."""

    role: Literal["user", "verity"]
    text: str
    steps: list[dict[str, Any]] = Field(default_factory=list)  # [{"tool": ..., "label": ..., "ok": ...}], Verity rows only
    reply: dict[str, Any] | None = None  # Verity rows only: {"check": ..., "reply": ...} as sent on the wire (capped by the store)


class ThreadTurnRow(BaseModel):
    """One stored message. A turn is one user row plus one verity row: turn n is seq 2n-1 and 2n."""

    seq: int
    role: Literal["user", "verity"]
    text: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    reply: dict[str, Any] | None = None  # the wire {"check", "reply"} when it was stored (turns before migration 7 have none)
    created_at: datetime


class ThreadData(BaseModel):
    """A thread as the agent reads it. `facts` is written by code only (verity.ask.memory); `summary` is the 20 to 40
    word note of every turn up to `summarized_through` (a turn row's seq). `turns` is every stored row, or with
    `since_summary` only the rows after `summarized_through`."""

    owner: str  # core.budget.spend_owner(session id): the session today, the user id after sign-in
    id: str  # a UUID made by the browser
    created_at: datetime
    updated_at: datetime
    facts: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    summarized_through: int = 0
    turns: list[ThreadTurnRow] = Field(default_factory=list)


class ThreadListItem(BaseModel):
    """One row of GET /threads."""

    id: str
    title: str  # thread_title() of the first user message
    updated_at: datetime
    run_ids: list[str] = Field(default_factory=list)


class UserRunRow(BaseModel):
    """One row of a user's run index (migration 7; GET /runs/mine): a run this owner started, and the thread it was in."""

    run_id: str
    title: str
    thread_id: str | None = None
    created_at: datetime
