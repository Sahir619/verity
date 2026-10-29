"""Per-run budget ledger (ARCHITECTURE 20). In-memory, synchronous, implements core.protocols.Ledger.

Flow for every LLM call (the gateway does this, see verity/llm/gateway.py):
    reservation = ledger.reserve(role=..., model=..., max_input_tokens=..., max_output_tokens=...)
    ... call the model ...
    ledger.commit(reservation, Usage(...))      # or ledger.release(reservation) on failure

`reserve` takes a WORST-CASE hold: committed spend plus every open reservation plus this one must stay inside
max_llm_calls, max_input_tokens, max_output_tokens and max_usd, and wall time must be under max_wall_s.
No overdraft "just this once".
"""

import itertools
import time
from collections.abc import Callable

from ..config import Settings
from ..core.types import BudgetExhausted, BudgetLimits, CountKind, Reservation, SpendSnapshot, Usage, price_usd


class RunLedger:
    def __init__(
        self,
        limits: BudgetLimits,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._settings = settings
        self._clock = clock
        self._started = clock()
        self._ids = itertools.count(1)
        self._open: dict[int, Reservation] = {}
        self._llm_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._usd = 0.0
        self._candidates = 0
        self._repairs = 0
        self._rounds = 0

    # ---------- reservations ----------
    def _elapsed(self) -> float:
        return self._clock() - self._started

    def reserve(self, *, role: str, model: str, max_input_tokens: int, max_output_tokens: int) -> Reservation:
        lim = self.limits
        if self._elapsed() >= lim.max_wall_s:
            raise BudgetExhausted("max_wall_s", f"{self._elapsed():.0f} s elapsed")

        price = self._settings.hold_price_for(model)  # dearest of the model and its fallback chain
        worst_usd = price_usd(price, max_input_tokens, max_output_tokens)

        open_calls = len(self._open)
        open_in = sum(r.max_input_tokens for r in self._open.values())
        open_out = sum(r.max_output_tokens for r in self._open.values())
        open_usd = sum(r.max_usd for r in self._open.values())

        if self._llm_calls + open_calls + 1 > lim.max_llm_calls:
            raise BudgetExhausted("max_llm_calls", f"{self._llm_calls} of {lim.max_llm_calls} calls used")
        if self._input_tokens + open_in + max_input_tokens > lim.max_input_tokens:
            raise BudgetExhausted("max_input_tokens", f"{self._input_tokens} of {lim.max_input_tokens} used")
        if self._output_tokens + open_out + max_output_tokens > lim.max_output_tokens:
            raise BudgetExhausted("max_output_tokens", f"{self._output_tokens} of {lim.max_output_tokens} used")
        if self._usd + open_usd + worst_usd > lim.max_usd:
            raise BudgetExhausted("max_usd", f"${self._usd:.4f} of ${lim.max_usd:.2f} used")

        reservation = Reservation(
            id=next(self._ids),
            role=role,
            model=model,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_usd=worst_usd,
        )
        self._open[reservation.id] = reservation
        return reservation

    def commit(self, reservation: Reservation, usage: Usage) -> None:
        """Replace the worst-case hold with what the call really used. Unknown or repeated commits are ignored."""
        if self._open.pop(reservation.id, None) is None:
            return
        self._llm_calls += usage.calls
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens
        self._usd += usage.usd

    def release(self, reservation: Reservation) -> None:
        self._open.pop(reservation.id, None)

    # ---------- counters ----------
    def can_count(self, kind: CountKind) -> bool:
        if kind == "candidate":
            return self._candidates < self.limits.max_candidates
        if kind == "round":
            return self._rounds < self.limits.max_rounds
        # repair: the per-candidate limit is the orchestrator's check (CONTRACTS S14).
        return True

    def count(self, kind: CountKind) -> None:
        if kind == "candidate":
            self._candidates += 1
        elif kind == "round":
            self._rounds += 1
        else:
            self._repairs += 1

    # ---------- state ----------
    def exhausted(self) -> str | None:
        """First limit that is used up by committed spend (LLM and wall-clock limits only).

        max_candidates and max_rounds are structural bounds of the search, not a reason to report cap_reached,
        so they are not reported here (use can_count for them).
        """
        lim = self.limits
        if self._elapsed() >= lim.max_wall_s:
            return "max_wall_s"
        if self._llm_calls >= lim.max_llm_calls:
            return "max_llm_calls"
        if self._input_tokens >= lim.max_input_tokens:
            return "max_input_tokens"
        if self._output_tokens >= lim.max_output_tokens:
            return "max_output_tokens"
        if self._usd >= lim.max_usd:
            return "max_usd"
        return None

    def snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            llm_calls=self._llm_calls,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            usd=self._usd,
            candidates=self._candidates,
            repairs=self._repairs,
            rounds=self._rounds,
            wall_s=round(self._elapsed(), 1),
        )
