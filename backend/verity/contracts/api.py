"""HTTP request/response bodies (frontend ARCHITECTURE 10, backend ARCHITECTURE 8). camelCase on the wire."""

import re
from typing import Literal

from pydantic import Field, field_validator

from .base import WireModel, opt
from .events import Priority

PathMode = Literal["fast", "full"]
_THREAD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def normalize_thread_id(value: str) -> str | None:
    """A thread id is a UUID in its written form (any version); the lowercase form is the one stored. None: not a UUID."""
    value = value.strip().lower()
    return value if _THREAD_ID.fullmatch(value) else None


# 429 reasons. The web maps busy | budget_exhausted | session_budget_exhausted | maintenance; anything else shows as
# "busy". session_budget_exhausted: this session's spend for the UTC day is used up ([limits] session_usd).
# sandbox_down is the one 503 reason: Docker is not reachable or the tool image is missing.
AdmissionReason = Literal[
    "busy", "budget_exhausted", "session_budget_exhausted", "credits_exhausted", "maintenance", "rate_limited", "sandbox_down"
]
HealthReason = Literal["budget_exhausted", "busy", "maintenance", "sandbox_down"]


class HealthResponse(WireModel):
    live_enabled: bool
    reason: HealthReason | None = opt()
    cap_usd: float
    provider: str
    models: dict[str, str]  # role -> model id
    auth_required: bool = False
    features: list[str] = []  # what this backend serves end to end, e.g. ["params"]


class SessionRequest(WireModel):
    access_code: str


class SessionResponse(WireModel):
    token: str
    expires_at: str  # ISO 8601 UTC


class AuthMeResponse(WireModel):
    email: str | None = opt()
    role: str
    credits: int
    is_admin: bool
    is_super_admin: bool = False
    can_build: bool  # signed in, so every kind of build is open and no credits are taken
    anonymous: bool = False


class AdminCreateUserRequest(WireModel):
    email: str
    password: str
    credits: int | None = opt()


class AdminCreateUserResponse(WireModel):
    ok: bool = True
    email: str
    credits: int
    user_id: str


class AdminSetCreditsRequest(WireModel):
    owner: str | None = opt()
    email: str | None = opt()
    credits: int | None = opt()
    add_credits: int | None = opt()


class AdminUserUsage(WireModel):
    owner: str
    credits: int
    email: str | None = opt()
    role: str = "user"
    runs_count: int = 0
    total_spent_usd: float = 0.0
    created_at: str | None = opt()
    updated_at: str | None = opt()


class AdminListUsersResponse(WireModel):
    users: list[AdminUserUsage]


class ResumeRunResponse(WireModel):
    ok: bool = True
    run_id: str
    status: str
    stream_token: str | None = opt()



class CreateRunRequest(WireModel):
    preset_id: str
    priority: Priority
    mode: PathMode | None = opt()
    params: dict[str, int | bool | str] | None = opt()  # knob values; omitted = the chip's defaults
    # The research id from POST /parse. The server checks it was handed to this session and matches the family and
    # knobs; a mismatch is ignored (the run starts without it), never an error.
    research_id: str | None = opt()
    # The thread this run was confirmed in (a UUID). When it is this session's thread, code adds the run id to the
    # thread's facts. Anything else is ignored: the run starts, never an error.
    thread_id: str | None = opt()

    @field_validator("research_id")
    @classmethod
    def _research_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if 1 <= len(value) <= 64 else None

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str | None) -> str | None:
        return normalize_thread_id(value) if value is not None else None


class CreateRunResponse(WireModel):
    run_id: str
    cap_usd: float
    stream_token: str


class ErrorResponse(WireModel):
    error: str
    reason: str | None = opt()


PARSE_MAX_CHARS = 300


class ParseRequest(WireModel):
    text: str

    @field_validator("text")
    @classmethod
    def _length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= PARSE_MAX_CHARS:
            raise ValueError(f"text must be 1 to {PARSE_MAX_CHARS} characters")
        return value


class ParseClosest(WireModel):
    family: str
    params: dict[str, int | bool | str]


# Fact check (plan claim 1). The verdict is set by code from the class and the validated family and knobs, never by
# a model. The intent class is the parser model's answer after code checks (the hardware-word guard turns a
# "not_chip" answer on a sentence with a hardware word into "unsure").
Verdict = Literal[
    "buildable_now", "buildable_with_changes", "not_buildable_here", "not_a_chip", "could_not_check", "custom_block", "cpu_choice"
]
# "custom_block" (CE3, D5x): reserved for a small block outside the four families that fits the custom limits.
# The type carries it end to end (frontend VERDICT record, VERDICTS list) so the wire shape is ready; POST /parse
# does not produce it yet (that needs its own classification work in the parser prompt, not done in this pass).
# draft_custom_block (the thread agent's own tool, backend/verity/ask/custom_draft.py) is the live CE3 path today.
IntentClass = Literal["not_chip", "question", "not_digital", "too_big", "in_family", "near_family", "unsure"]
SourceTier = Literal["A", "B", "C", "P"]  # A papers and standards, B vendors and universities, C open-edit, P patents
SearchEndpoint = Literal["search", "scholar", "patents"]
ResearchStatus = Literal["ok", "partial", "failed"]


class ResearchSentence(WireModel):
    text: str
    source_ids: list[int]  # the n of sources (S1 to S8); code dropped every other id


class ResearchSource(WireModel):
    n: int
    url: str
    title: str
    domain: str
    tier: SourceTier
    date: str | None = None
    endpoint: SearchEndpoint


class ResearchTechnique(WireModel):
    id: str  # from the fixed per-family list (verity.research.techniques) only
    label: str  # our own one-line text, never web text


class Research(WireModel):
    id: str
    status: ResearchStatus
    reused: bool
    created_at: str  # ISO 8601 UTC
    queries: list[str]  # the exact text sent to Serper ("Searched: ...")
    sentences: list[ResearchSentence]
    sources: list[ResearchSource]
    techniques: list[ResearchTechnique]
    done_before: bool
    past_runs: int  # comparable earlier Verity runs (same family, knobs, pack and tool image)
    usd: float  # what THIS request spent on research (0 when reused)
    serper_credits: int  # Serper credits THIS request used (0 when reused)
    tavily_credits: int | None = opt()  # Tavily credits THIS request used (0 when reused); omitted when Tavily is not in use


class ParseInRange(WireModel):
    result: Literal["in_range"] = "in_range"
    family: str
    params: dict[str, int | bool | str]
    note: str | None = opt()
    verdict: Verdict | None = opt()
    intent_class: IntentClass | None = opt()
    research: Research | None = None  # null when no research ran (not_chip, rate limited) or research is off
    # The knob ids the sentence itself stated, confirmed by code against the text (never the model's word alone). A knob
    # that is not listed was not stated: its value is the chip's default or the model's reading, and the UI shows it as
    # an assumption. Omitted by older servers.
    stated_knobs: list[str] | None = opt()


class CpuKind(WireModel):
    id: str
    name: str
    summary: str
    buildable: bool


class ParseOutOfRange(WireModel):
    result: Literal["out_of_range"] = "out_of_range"
    reason: str
    closest: ParseClosest | None = None  # always on the wire; null when nothing is close
    verdict: Verdict | None = opt()
    intent_class: IntentClass | None = opt()
    research: Research | None = None
    stated_knobs: list[str] | None = opt()  # as on ParseInRange, for the knobs of `closest`
    cpu_kinds: list[CpuKind] | None = opt()  # populated only from the fixed CPU catalog


ParseReply = ParseInRange | ParseOutOfRange


# POST /runs/{id}/ask (plan claim 3): a question or a change request about a finished run, from its owner session.
ASK_MAX_CHARS = 300
_VERSION_LABEL = re.compile(r"^v\d{1,3}$")


class AskRequest(WireModel):
    text: str
    focus_version: str | None = opt()  # the version the user is looking at, like "v2"; anything else is ignored

    @field_validator("text")
    @classmethod
    def _length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= ASK_MAX_CHARS:
            raise ValueError(f"text must be 1 to {ASK_MAX_CHARS} characters")
        return value

    @field_validator("focus_version")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value if _VERSION_LABEL.fullmatch(value) else None


# The nine steps of the engineer's walkthrough, in order (POST /runs/{id}/ask "walk me through it").
WalkStepId = Literal["spec", "plan", "rtl", "lint", "simulate", "synth", "improve", "equivalence", "signoff"]
WALK_STEP_ORDER: tuple[str, ...] = ("spec", "plan", "rtl", "lint", "simulate", "synth", "improve", "equivalence", "signoff")


class AskStep(WireModel):
    step_id: WalkStepId
    title: str
    text: str  # built by code from run data, every number from the run
    candidate_id: str | None = opt()  # the version the step is about ("c1"), when it is about one
    node_ids: list[str] | None = opt()  # ids in that version's circuit/<candidateId>.json artifact, only honest matches


class AskAnswer(WireModel):
    kind: Literal["answer"] = "answer"
    text: str
    bullets: list[str] = []
    basis: str  # where the answer comes from, e.g. "From this run: versions, tests, reports and the tie rule."
    templated: bool  # true: written by code; false: worded by the chat model, every number checked by code
    steps: list[AskStep] | None = opt()  # a walkthrough: the nine steps in order (bullets is empty then)


class RunProposal(WireModel):
    preset_id: str
    params: dict[str, int | bool | str]  # validated knobs, defaults filled in
    priority: Priority
    mode: PathMode | None = opt()
    title: str
    idea: str | None = None  # the technique the new run's EXPLORE version tries first (from research it reads), or null
    last_run_usd: float | None = None
    cap_usd: float


class AskProposal(WireModel):
    kind: Literal["proposal"] = "proposal"
    text: str
    proposal: RunProposal


# ---------- CE3: custom blocks ----------
CustomBlockElementType = Literal["int4", "uint4", "int8", "uint8", "none"]


class CustomBlockPort(WireModel):
    name: str
    dir: Literal["in", "out"]
    width: int = Field(ge=1, le=64)  # CE3 limit: narrower than a locked pack's 1024
    count: int = Field(default=1, ge=1, le=1)  # CE3 limit: no lane ports
    signed: bool = False


class CustomBlockStep(WireModel):
    inputs: dict[str, int]
    rst: bool = False
    expected: dict[str, int]


class CustomBlockCase(WireModel):
    name: str
    steps: list[CustomBlockStep] = Field(min_length=1)


class CustomBlockDraft(WireModel):
    """GLM's drafted spec, worked examples and golden model for one custom block (CE3). Round-trips through the
    browser between draft_custom_block and POST /custom-blocks/approve; the server re-validates and re-runs the
    self-check at Approve regardless of what the browser sends back."""

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    summary: str
    spec_text: str
    top: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    element_type: CustomBlockElementType = "none"
    latency_cycles: int = Field(ge=0, le=8)  # CE3 limit
    ports: list[CustomBlockPort] = Field(min_length=1, max_length=8)  # CE3 limit
    clock_name: str = ""  # "" for a purely combinational block
    reset_name: str = ""  # "" for no reset
    golden_py: str
    cases: list[CustomBlockCase] = Field(min_length=1)


class ApproveCustomBlockRequest(WireModel):
    draft: CustomBlockDraft


class ApproveCustomBlockResponse(WireModel):
    ok: bool = True
    preset_id: str


class AskNewChip(WireModel):
    kind: Literal["new_chip"] = "new_chip"  # a different chip: the UI offers a new thread and fact-checks it there
    text: str


class AskRefused(WireModel):
    kind: Literal["refused"] = "refused"  # off topic
    text: str


AskReply = AskAnswer | AskProposal | AskNewChip | AskRefused


# POST /threads/{threadId}/messages: every message after the first chip request is a conversation with the agent
# (backend ARCHITECTURE 17.4). The first message of a thread still runs the fact check, inside the same call.
THREAD_MAX_CHARS = 300
ToolName = Literal[
    "list_blocks", "research", "run_facts", "walkthrough", "propose_run", "open_tab", "draft_custom_block",
    "propose_cpu_job", "propose_cpu_retry", "propose_cpu_upgrade",
]
TabName = Literal["result", "versions", "verilog", "architecture", "signoff"]


class ToolStep(WireModel):
    tool: ToolName
    label: str  # written by code, e.g. "Read the build list" or "Looked up the timing of v4"
    ok: bool  # false: code refused the step (bad arguments, a run that is not this thread's, a limit)


class OpenTab(WireModel):
    run_id: str
    tab: TabName
    version: str | None = opt()


class AgentAnswer(WireModel):
    kind: Literal["answer"] = "answer"
    text: str
    bullets: list[str] = []
    basis: str  # where the answer comes from
    templated: bool  # true: written by code; false: worded by the chat model, every number checked by code
    steps: list[AskStep] | None = opt()  # a walkthrough: the nine steps in order (bullets is empty then)
    tool_steps: list[ToolStep] = []
    chips: list[str] | None = opt()  # short phrases the user can tap (block names, follow-ups); the web sends the chip text as the next message
    open_tab: OpenTab | None = opt()
    research: Research | None = opt()


class AgentProposal(WireModel):
    kind: Literal["proposal"] = "proposal"
    text: str
    proposal: RunProposal  # nothing runs until the user presses Run: the web posts it to POST /runs
    tool_steps: list[ToolStep] = []


class CpuAction(WireModel):
    """What a CPU card asks the user to confirm, written by code after the job manager's own checks passed. new_job: job_id
    and upgrade_id are null. retry_core: job_id is the failed job. upgrade: job_id is the passed parent, upgrade_id is set."""

    type: Literal["new_job", "retry_core", "upgrade"]
    goal: Priority
    job_id: str | None
    upgrade_id: Literal["pipeline_2", "cache_16"] | None
    title: str  # written by code
    summary: str  # written by code: what Nemotron will build and what is reused
    max_usd: float  # the CPU job spend cap from config ([cpu_job] max_usd)


class AgentCpuProposal(WireModel):
    kind: Literal["cpu_proposal"] = "cpu_proposal"
    text: str  # the agent's own words: what and why
    action: CpuAction  # nothing starts until the user presses Confirm: the web posts the CPU job route, which checks again
    tool_steps: list[ToolStep] = []


class AgentRefused(WireModel):
    kind: Literal["refused"] = "refused"
    text: str
    tool_steps: list[ToolStep] = []


AgentReply = AgentAnswer | AgentProposal | AgentCpuProposal | AgentRefused


class ThreadMessageRequest(WireModel):
    text: str
    run_id: str | None = opt()  # the run this thread is about; must be this session's
    focus_version: str | None = opt()  # the version the user has open, like "v2"; anything else is ignored
    cpu_job_id: str | None = opt()  # the CPU job whose board is on screen; the route drops it unless it is this account's

    @field_validator("text")
    @classmethod
    def _length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= THREAD_MAX_CHARS:
            raise ValueError(f"text must be 1 to {THREAD_MAX_CHARS} characters")
        return value

    @field_validator("focus_version")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value if _VERSION_LABEL.fullmatch(value) else None


class ThreadMessageReply(WireModel):
    thread_id: str
    check: ParseReply | None = None  # exactly the POST /parse reply; only when this message ran the fact check
    reply: AgentReply | None = None  # null only when check.verdict is "not_a_chip"


class ThreadListEntry(WireModel):
    id: str
    title: str  # the first user message, cut to 60 characters
    updated_at: str  # ISO 8601 UTC
    run_ids: list[str]


class ThreadsResponse(WireModel):
    threads: list[ThreadListEntry]  # newest first, at most 50


class ThreadTurn(WireModel):
    seq: int  # a turn is two rows: the user's (odd seq) then Verity's (even seq)
    role: Literal["user", "verity"]
    text: str  # as stored: the user's at most 300 characters, Verity's at most 1,200
    tool_steps: list[ToolStep] | None = opt()  # on Verity's rows only
    # On Verity's rows only, and only when they were stored (turns from before accounts, and replies over 32 KB, have
    # none): the reply exactly as POST /threads/{id}/messages sent it, and the fact check that message ran, if any.
    reply: AgentReply | None = opt()
    check: ParseReply | None = opt()
    created_at: str  # ISO 8601 UTC


class ThreadView(WireModel):
    thread_id: str
    run_ids: list[str]
    turns: list[ThreadTurn]  # every stored row, oldest first


class MyRun(WireModel):
    """One row of GET /runs/mine. `status` is null when the run's files are no longer on this server."""

    id: str
    title: str
    thread_id: str | None  # null when the run did not start from a thread
    created_at: str  # ISO 8601 UTC
    status: str | None


class MyRunsResponse(WireModel):
    runs: list[MyRun]  # newest first, at most 50


class AdminRunsResponse(WireModel):
    """GET /admin/runs?owner=<owner or email>. Same shape as GET /runs/mine, for an admin looking at one user."""

    owner: str
    email: str | None = opt()
    runs: list[MyRun]  # newest first, at most 50


class RunWinnerSummary(WireModel):
    label: str
    area_um2: float | None = None
    clock_period_ns: float | None = None


class RunSignoffSummary(WireModel):
    """The sign-off sheet of the winner, in two numbers (the locked tests on every seed that ran)."""

    seeds_passed: int
    seeds_run: int


class RunOutcomeSummary(WireModel):
    """From the run_outcome table. The three ratios are the winner against THIS run's own v1 (1.0 = same as v1, lower is
    better), null when unknown. `usd` is the run's actual spend."""

    area_vs_v1: float | None = None
    clock_vs_v1: float | None = None
    power_vs_v1: float | None = None
    versions: int
    usd: float
    signoff: RunSignoffSummary | None = opt()  # only when a sign-off sheet exists for the winner


class RunSummaryItem(WireModel):
    id: str
    preset_id: str
    params: dict[str, int | bool | str] | None = None
    title: str
    status: str
    finished_at: str | None = None
    winner: RunWinnerSummary | None = None
    outcome: RunOutcomeSummary | None = opt()  # absent for a run that has no run_outcome row (still going, or older)


class RunsResponse(WireModel):
    runs: list[RunSummaryItem]
