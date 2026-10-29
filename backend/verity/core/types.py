"""One import point for backend-internal value objects and protocols.

    from verity.core.types import Rtl, TaskPack, VerifyResult, RunContext, ToolRunner, ...

Definitions live in small sibling modules:
  rtl.py        Rtl value object + RTL validation rules (ARCHITECTURE 12.3)
  pack.py       TaskPack, TaskPort, manifest hashing, coding rules (ARCHITECTURE 13)
  results.py    sandbox result JSON models and their mapping to Verification / PPA / Equivalence
  budget.py     BudgetLimits, Usage, Reservation, SpendSnapshot, price_usd (ARCHITECTURE 20)
  llm_types.py  LLMRequest, LLMResponse, CallMeta, PlanOut, PlanResult, RtlOut (ARCHITECTURE 21, 10.1)
  records.py    Store records: RunRecord, RunPatch, AttemptMeta, ArtifactRef, EvalResultRow (ARCHITECTURE 7)
  protocols.py  LLMProvider, LLMGateway, role fns, ToolRunner, Ledger, EventSink, Store, ThreadStore, RunService
  context.py    RunContext, Roles, RunOutcome
  fitness.py    compute_fitness, select_elites, pick_winner (frontend ARCHITECTURE 8.3)
  captions.py   caption templates and chapter labels
  errors.py     shared exceptions
"""

from ..config import PathMode, RoleName, StrategyName
from ..contracts import CircuitGraph
from .budget import BudgetLimits, CountKind, DailySpend, LimitName, Reservation, SpendSnapshot, Usage, estimate_tokens, price_usd
from .context import Roles, RunContext, RunOutcome
from .errors import (
    AdmissionDenied,
    BudgetExhausted,
    Conflict,
    FeasibilityRejected,
    Forbidden,
    LLMError,
    NotFound,
    PackFormatError,
    PackIntegrityError,
    RoleOutputError,
    RtlRejected,
    RunStopped,
    ToolInfraError,
    UnknownPreset,
    VerityError,
)
from .fitness import BASE_WEIGHTS, compute_fitness, pick_winner, rankable, select_elites
from .llm_types import CallMeta, LLMRequest, LLMResponse, PlanOut, PlanResult, ResponseFormat, RtlOut
from .pack import (
    CODING_RULES,
    ClockSpec,
    ElementType,
    EqySpec,
    ResetSpec,
    SimSpec,
    TaskPack,
    TaskPort,
    load_packs,
    verify_manifest,
    write_manifest,
)
from .protocols import (
    EventSink,
    GenerateRtlFn,
    Ledger,
    LLMGateway,
    LLMProvider,
    OptimizeFn,
    PlanFn,
    RepairFn,
    RunService,
    Store,
    ThreadStore,
    ToolRunner,
)
from .records import ACTIVE_STATUSES, ArtifactRef, AttemptMeta, EvalResultRow, RunPatch, RunRecord, RunStatus
from .results import (
    MAX_FAILURES,
    ContractResult,
    EquivalenceResult,
    LintResult,
    MeasureResult,
    ParsedPort,
    PowerResult,
    SimCase,
    SimResult,
    SynthResult,
    TimingResult,
    VerifyResult,
    not_run_equivalence,
)
from .rtl import Rtl, find_problems, normalize_rtl_text, strip_comments

__all__ = [name for name in dir() if not name.startswith("_")]
