"""Exceptions shared across builders. The API layer maps them to HTTP codes (see CONTRACTS.md)."""


class VerityError(Exception):
    """Base class."""


# ---------- task packs and RTL ----------
class PackIntegrityError(VerityError):
    """A task pack file does not match MANIFEST.sha256, or the manifest is missing/incomplete. Aborts the run."""


class PackFormatError(VerityError):
    """task.toml is missing a field or has an invalid value."""


class RtlRejected(VerityError):
    """RTL failed value-object validation (ARCHITECTURE 12.3) before reaching any sandbox.

    The orchestrator records this as a failed attempt (lint "fail", lintLog lists `problems`) and it counts
    as a repair attempt.
    """

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# ---------- budget and run control ----------
class BudgetExhausted(VerityError):
    """Ledger refused a reservation. `limit` names the limit that would be crossed (e.g. "max_usd")."""

    def __init__(self, limit: str, detail: str = ""):
        self.limit = limit
        self.detail = detail
        super().__init__(f"budget limit reached: {limit}" + (f" ({detail})" if detail else ""))


class RunStopped(VerityError):
    """Raised by RunContext.check_stop() when the user pressed Stop."""


class CreditsExhausted(VerityError):
    """Raised when an active run detects that user credits are exhausted."""


class FeasibilityRejected(VerityError):
    """The deterministic feasibility gate rejected the plan (ARCHITECTURE 10.2)."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# ---------- LLM ----------
class LLMError(VerityError):
    """Provider call failed after retries, timed out, or returned nothing usable."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        self.retryable = retryable
        self.status = status
        super().__init__(message)


class RoleOutputError(VerityError):
    """The model answered but the role could not parse a plan / Verilog block from it (after its retry)."""

    def __init__(self, role: str, message: str, raw: str = ""):
        self.role = role
        self.raw = raw
        super().__init__(f"{role}: {message}")


# ---------- tools ----------
class ToolInfraError(VerityError):
    """The sandbox itself failed (docker missing, API error, exit code 2/3, unparseable output).

    Never raised for a design that fails its tests. The runner retries once (tools.infra_retries) before raising.
    """

    def __init__(self, message: str, *, script: str = "", exit_code: int | None = None, stderr_tail: str = ""):
        self.script = script
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        super().__init__(message)


# ---------- API-facing ----------
class AdmissionDenied(VerityError):
    """POST /runs refused. HTTP 429 {reason}, or 503 for sandbox_down (message: the plain sentence to show)."""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason  # busy | budget_exhausted | session_budget_exhausted | maintenance | rate_limited | sandbox_down
        super().__init__(message or reason)


class NotFound(VerityError):
    """HTTP 404."""


class Forbidden(VerityError):
    """HTTP 403 (run owned by another session)."""


class Conflict(VerityError):
    """HTTP 409 (e.g. steering a finished run, or a pin after the gate closed)."""


class UnknownPreset(VerityError):
    """HTTP 422."""


class StoreUnavailable(VerityError):
    """The Supabase store for chats and run lists could not be reached or refused a call. HTTP 502. The message is
    fixed text for the user; the cause is logged without keys by the store."""

    def __init__(self, message: str = "Saved chats are unreachable right now."):
        super().__init__(message)
