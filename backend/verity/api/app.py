"""FastAPI app factory (CONTRACTS section 1 and 9).

    app = create_app(settings, service)

`service` is the RunManager. Resource setup that must happen inside the server's event loop (opening the
store, closing the LLM client) is passed in as `startup` and `shutdown` hooks by verity.main.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from ..config import Settings
from ..core.protocols import RunService
from .auth import AuthService
from .errors import install_error_handlers
from .ratelimit import TokenBucketLimiter
from .routes import ApiState, ip_guard, new_limiters, router

log = logging.getLogger("verity.api")

Hook = Callable[[], Awaitable[None]]


class SecurityHeadersMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware, which interferes with streaming responses and disconnects).

    Referrer-Policy matters because the SSE URL carries a stream token in its query string.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    settings: Settings,
    service: RunService,
    *,
    startup: Sequence[Hook] = (),
    shutdown: Sequence[Hook] = (),
) -> FastAPI:
    """Build the app. Lifespan order: `startup` hooks, service.start(); then on exit service.shutdown() and the
    `shutdown` hooks in reverse order (each runs even if an earlier one failed)."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        service_started = False
        try:
            for hook in startup:
                await hook()
            await service.start()
            service_started = True
            yield
        finally:
            if service_started:
                try:
                    await service.shutdown()
                except Exception:
                    log.exception("run service shutdown failed")
            for hook in reversed(shutdown):
                try:
                    await hook()
                except Exception:
                    log.exception("shutdown hook failed")
            try:
                await application.state.api.auth.aclose()
            except Exception:
                log.exception("auth client close failed")

    production = settings.env == "production"
    app = FastAPI(
        title="Verity agent",
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(ip_guard)],
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )

    ip_limiter, session_limiter, auth_limiter, run_limiter = new_limiters(settings)
    app.state.api = ApiState(
        settings=settings,
        service=service,
        auth=AuthService(settings),
        ip_limiter=ip_limiter,
        session_limiter=session_limiter,
        auth_limiter=auth_limiter,
        run_limiter=run_limiter,
        parse_limiter=TokenBucketLimiter(settings.limits.parse_per_session_per_min, 60.0),
        ask_limiter=TokenBucketLimiter(settings.limits.ask_per_session_per_min, 60.0),
        message_limiter=TokenBucketLimiter(settings.limits.message_per_session_per_min, 60.0),
    )

    install_error_handlers(app)

    # Added last = outermost, so CORS headers are present on every response, including errors.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        allow_credentials=False,
        max_age=600,
    )

    app.include_router(router)

    if not settings.auth.required:
        log.warning("auth.required is false: anonymous sessions keyed by client IP. Not for production.")
    elif not settings.access_codes:
        log.warning("auth.required is true but VERITY_ACCESS_CODE_HASHES is empty: nobody can log in")
    return app
