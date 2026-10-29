"""Budget value objects (ARCHITECTURE 20). The Ledger implementation lives in verity/runs (builder D)."""

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..config import Price, RunBudget, Settings

# What the ledger counts besides LLM calls/tokens/dollars.
CountKind = Literal["candidate", "repair", "round"]
LimitName = Literal[
    "max_llm_calls",
    "max_input_tokens",
    "max_output_tokens",
    "max_usd",
    "max_candidates",
    "max_repairs",
    "max_rounds",
    "max_wall_s",
]


class BudgetLimits(BaseModel):
    """The limits one run was admitted with. Stored as runs.budget_json."""

    max_llm_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_usd: float
    max_candidates: int
    max_repairs_baseline: int
    max_repairs_child: int
    max_rounds: int
    max_wall_s: int

    @classmethod
    def from_settings(cls, settings: Settings, overrides: dict[str, float] | None = None) -> "BudgetLimits":
        """[budget.run] defaults, then the task pack's [budget] overrides (which may only LOWER a limit)."""
        base: RunBudget = settings.budget.run
        data = base.model_dump()
        for key, value in (overrides or {}).items():
            if key in data:
                data[key] = type(data[key])(min(data[key], value))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Usage:
    """What one finished LLM call cost. `usd` is estimated from the config price table."""

    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    calls: int = 1


@dataclass(frozen=True, slots=True)
class Reservation:
    """Worst-case hold taken before a call. Commit with the real Usage or release on failure."""

    id: int
    role: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    max_usd: float


class SpendSnapshot(BaseModel):
    """runs.spent_json and the source of `credits` events."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    candidates: int = 0
    repairs: int = 0
    rounds: int = 0
    wall_s: float = 0.0


def price_usd(price: Price, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price.input_per_m + output_tokens * price.output_per_m) / 1_000_000


def estimate_tokens(text: str) -> int:
    """Conservative prompt-size estimate used for reservations before the real usage is known (about 3.5 chars/token)."""
    return math.ceil(len(text) / 3.5) + 16


def spend_owner(session_id: str) -> str:
    """Whose per-day spend limit a request counts against ([limits] session_usd), and who owns threads and the run list.
    That is the session id. A signed-in user's session id is "u:" + the Supabase user id (api.auth), the same on every
    device, so the owner already is the user; a judge access code or an anonymous visit has its own session id. If
    owners ever need another key, change this one function: the store keys session_spend by it and the run manager
    matches holds by it."""
    return session_id


@dataclass(slots=True)
class DailySpend:
    """Helper for admission: today's persisted spend vs a daily limit (the global [limits] daily_usd, or one owner's
    session_usd; the same arithmetic for both)."""

    day: str  # UTC "YYYY-MM-DD"
    usd: float
    limit_usd: float
    reserved: list[float] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        return self.usd >= self.limit_usd

    def can_admit(self, run_cap_usd: float) -> bool:
        return self.usd + sum(self.reserved) + run_cap_usd <= self.limit_usd
