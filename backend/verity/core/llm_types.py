"""LLM boundary types (ARCHITECTURE 21) and role outputs (ARCHITECTURE 10.1, 17)."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from ..config import RoleName
from ..contracts import PPA, PlanBlock, WireModel

ResponseFormat = Literal["text", "json"]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    role: RoleName
    model: str  # resolved from the role route by the gateway
    system: str
    user: str
    max_output_tokens: int
    temperature: float
    top_p: float | None = None  # None: the provider default (not sent)
    response_format: ResponseFormat = "text"
    timeout_s: float = 120.0
    # Non-prompt metadata: task_id, strategy, attempt, prompt_version. Used by FakeProvider cassettes and logs.
    # Never sent to the model.
    tags: dict[str, str] = field(default_factory=dict)
    # None: the model default. False: ask the endpoint not to reason (chat_template_kwargs.enable_thinking). The
    # provider sends it only to models that take the switch (llm.thinking_switch_prefixes).
    thinking: bool | None = None
    # None: not sent. "low" | "medium" | "high": sent as reasoning_effort (GLM 5.3 always reasons; low keeps it short).
    reasoning_effort: str | None = None
    # Seconds this whole call may take, fallback models included (the thread agent passes the time left in its turn). A
    # fallback attempt gets at most what is left and is skipped with under 5 s left. None: no limit beyond timeout_s.
    deadline_s: float | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int  # includes reasoning tokens when the API reports them separately
    model: str  # the model id the endpoint says it served (may differ from the requested id)
    request_id: str
    latency_ms: int
    reasoning_tokens: int = 0
    finish_reason: str = ""


@dataclass(frozen=True, slots=True)
class CallMeta:
    """Recorded per attempt (ARCHITECTURE 6, 15)."""

    role: RoleName
    model: str
    request_id: str
    prompt_version: str  # "<template file name>@<sha256[:12] of the template>"
    input_tokens: int
    output_tokens: int
    latency_ms: int
    usd: float
    finish_reason: str = ""  # "stop", "length" (cut at the output cap), ...; comma separated when calls were merged
    reasoning_tokens: int = 0  # already inside output_tokens


# ---------- role outputs ----------
class PlanOut(WireModel):
    """The Planner's JSON (ARCHITECTURE 10.1). camelCase keys in the model's JSON, snake_case here."""

    summary: str
    blocks: list[PlanBlock]
    interfaces: str = ""
    data_widths: str = ""
    latency_cycles: int
    verification: str = ""
    risks: list[str] = Field(default_factory=list)
    estimated_lines: int


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan: PlanOut
    meta: CallMeta
    raw: str  # full model text, stored as an artifact for debugging only


@dataclass(frozen=True, slots=True)
class TriedVersion:
    """An earlier version of the same run, for the optimizer's "already tried" list. `ppa` is None when it was
    rejected. `reason` is the version's own one-line reason (our code writes "Same result as v3" for a copy)."""

    label: str
    strategy: str
    reason: str
    ppa: PPA | None


@dataclass(frozen=True, slots=True)
class RtlOut:
    """RTL text as extracted from the model, NOT yet validated. The orchestrator calls pack.validate_rtl()."""

    text: str
    reason: str  # one sentence, max 200 chars (role truncates)
    meta: CallMeta
    raw: str
    diagnosis: str | None = None  # repair only: one-paragraph reading of the failures, max 300 chars
