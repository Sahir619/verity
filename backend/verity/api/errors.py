"""Error bodies and exception handlers. Every error is an `ErrorResponse` {error, reason?} (CONTRACTS section 9).

Messages are fixed strings chosen here. Input values, tokens and exception internals are never echoed.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..contracts import ErrorResponse, to_wire
from ..core import captions as cap
from ..core.errors import AdmissionDenied, Conflict, Forbidden, NotFound, StoreUnavailable, UnknownPreset

log = logging.getLogger("verity.api")

_ADMISSION_TEXT = {
    "busy": "Another live run is in progress.",
    "budget_exhausted": "Live runs are paused: the daily budget is used up.",
    # No dollar figure in the text: the UI shows what was spent, never the limit.
    "session_budget_exhausted": "You have reached today's spend limit for this session. Try again tomorrow.",
    "credits_exhausted": "You have used all your run credits. Contact an admin to add more.",
    "maintenance": "Live runs are offline for maintenance.",
    "rate_limited": "Too many requests. Try again shortly.",
    "sandbox_down": cap.DOCKER_DOWN,
}
# The sandbox check picks one of these sentences; anything else falls back to the fixed text above.
_SANDBOX_TEXT = frozenset({cap.DOCKER_DOWN, cap.IMAGE_MISSING})


class ApiError(Exception):
    """Raised by routes and dependencies; rendered as an ErrorResponse."""

    def __init__(
        self,
        status: int,
        error: str,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.reason = reason
        self.headers = headers or {}
        super().__init__(error)


def error_response(
    status: int, error: str, reason: str | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = to_wire(ErrorResponse(error=error, reason=reason))
    return JSONResponse(body, status_code=status, headers=headers)


def rate_limited(retry_after_s: float) -> ApiError:
    wait = max(1, int(retry_after_s) + 1)
    return ApiError(429, _ADMISSION_TEXT["rate_limited"], "rate_limited", {"Retry-After": str(wait)})


def unauthorized(message: str = "Authentication required.") -> ApiError:
    return ApiError(401, message, None, {"WWW-Authenticate": "Bearer"})


def validation_summary(locations: list[Any]) -> str:
    names = sorted({".".join(str(part) for part in loc if part not in ("body", "query")) for loc in locations})
    names = [n for n in names if n]
    return "Invalid request" + (f": check {', '.join(names)}." if names else ".")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.error, exc.reason, exc.headers)

    @app.exception_handler(AdmissionDenied)
    async def _admission(_: Request, exc: AdmissionDenied) -> JSONResponse:
        if exc.reason == "sandbox_down":  # the server cannot run tests right now: 503, not a client limit
            text = str(exc) if str(exc) in _SANDBOX_TEXT else _ADMISSION_TEXT["sandbox_down"]
            return error_response(503, text, "sandbox_down")
        reason = exc.reason if exc.reason in _ADMISSION_TEXT else "busy"
        return error_response(429, _ADMISSION_TEXT[reason], reason)

    @app.exception_handler(UnknownPreset)
    async def _unknown_preset(_: Request, __: UnknownPreset) -> JSONResponse:
        return error_response(422, "Unknown preset.")

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, __: NotFound) -> JSONResponse:
        return error_response(404, "Run not found.")

    @app.exception_handler(Forbidden)
    async def _forbidden(_: Request, __: Forbidden) -> JSONResponse:
        return error_response(403, "This run belongs to another session.")

    @app.exception_handler(StoreUnavailable)
    async def _store_unavailable(_: Request, exc: StoreUnavailable) -> JSONResponse:
        return error_response(502, str(exc))

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        text = str(exc).strip()[:200] or "The run can no longer accept this input."
        return error_response(409, text)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, validation_summary([e.get("loc", ()) for e in exc.errors()]))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        text = {404: "Not found.", 405: "Method not allowed."}.get(exc.status_code, "Request failed.")
        return error_response(exc.status_code, text, None, dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Method and path only (no query string, which can carry a stream token); the traceback has no headers.
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc)
        return error_response(500, "Internal error.")
