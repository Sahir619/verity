"""HTTP routes (CONTRACTS section 9). Auth, ownership and rate limits are applied here; run logic is the RunService's."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TypeVar

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..contracts import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminListUsersResponse,
    AdminRunsResponse,
    AdminSetCreditsRequest,
    AdminUserUsage,
    ApproveCustomBlockRequest,
    ApproveCustomBlockResponse,
    AskRequest,
    AuthMeResponse,
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    ParseRequest,
    ResumeRunResponse,
    SessionRequest,
    SessionResponse,
    ThreadMessageRequest,
    normalize_thread_id,
    to_json,
    to_wire,
)
from ..contracts.view_types import STEER_ADAPTER
from ..contracts.cpu import CreateCpuJobRequest, CpuJobsResponse, UpgradeRequest
from ..core.errors import AdmissionDenied, Conflict, Forbidden, LLMError, NotFound
from ..core.family import ParamsError
from ..core.protocols import RunService
from ..core.records import ACTIVE_STATUSES, RunRecord
from ..runs.custom_blocks import ApproveFailed
from .auth import AdminCreateUserError, AuthService, Principal, anonymous_sid, iso_z
from .errors import ApiError, rate_limited, unauthorized, validation_summary
from .ratelimit import TokenBucketLimiter
from .sse import FINISHED_RETRY_MS, RETRY_MS, close_stream, sse_body

log = logging.getLogger("verity.api")

router = APIRouter()

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CPU_JOB_ID_RE = re.compile(r"^cpu_[0-9a-f]{32}$")
MAX_BODY_BYTES = 8 * 1024
MAX_STEER_BYTES = 4 * 1024
_PEEK_TIMEOUT_S = 10.0

M = TypeVar("M", bound=BaseModel)


@dataclass(slots=True)
class ApiState:
    settings: Settings
    service: RunService
    auth: AuthService
    ip_limiter: TokenBucketLimiter
    session_limiter: TokenBucketLimiter
    auth_limiter: TokenBucketLimiter
    run_limiter: TokenBucketLimiter
    parse_limiter: TokenBucketLimiter
    ask_limiter: TokenBucketLimiter
    message_limiter: TokenBucketLimiter


def _state(request: Request) -> ApiState:
    return request.app.state.api  # type: ignore[no-any-return]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------- dependencies ----------
async def ip_guard(request: Request) -> None:
    """Applied to every route: per-IP request bucket."""
    wait = _state(request).ip_limiter.acquire(client_ip(request))
    if wait > 0:
        raise rate_limited(wait)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


async def _principal(request: Request, state: ApiState) -> Principal:
    token = _bearer(request)
    if token:
        principal = await state.auth.authenticate(token)  # a Verity session token, else a Supabase access token
        if principal is not None:
            return principal
    if state.settings.auth.required:
        raise unauthorized("Missing or invalid session token.")
    return _anonymous(request, state)


async def _principal_opt(request: Request, state: ApiState) -> Principal | None:
    token = _bearer(request)
    if token:
        return await state.auth.authenticate(token)
    if not state.settings.auth.required:
        return _anonymous(request, state)
    return None


def _anonymous(request: Request, state: ApiState) -> Principal:
    """A visitor without a session (auth off). auth.local_admin makes it a super admin, on a development server only."""
    local = state.settings.auth.local_admin and state.settings.env == "development"
    return Principal(sid=anonymous_sid(client_ip(request)), role="super_admin" if local else "anonymous", anonymous=True)


def _charge_session(state: ApiState, sid: str) -> None:
    wait = state.session_limiter.acquire(sid)
    if wait > 0:
        raise rate_limited(wait)


async def session_guard(request: Request) -> Principal:
    """Session from the bearer token (or an anonymous one when auth is off), then the per-session bucket."""
    state = _state(request)
    principal = await _principal(request, state)
    _charge_session(state, principal.sid)
    return principal


# ---------- helpers ----------
async def _read_raw(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "Request body too large.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "Request body too large.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_json(request: Request, model: type[M], limit: int = MAX_BODY_BYTES) -> M:
    raw = await _read_raw(request, limit)
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(422, validation_summary([e["loc"] for e in exc.errors()])) from None


def _valid_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ApiError(404, "Run not found.")
    return run_id


async def _owned_run(state: ApiState, run_id: str, sid: str) -> RunRecord:
    run = await state.service.get_run(_valid_run_id(run_id))
    if run is None:
        raise ApiError(404, "Run not found.")
    if run.session_id != sid:
        raise ApiError(403, "This run belongs to another session.")
    return run


def _valid_thread_id(thread_id: str) -> str:
    """A thread id is a UUID (any version); anything else is a 422. The lowercase form is the one stored."""
    normal = normalize_thread_id(thread_id)
    if normal is None:
        raise ApiError(422, "The thread id must be a UUID.")
    return normal


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(to_wire(model), status_code=status, headers={"Cache-Control": "no-store"})


# ---------- routes ----------
@router.post("/cpu-jobs")
async def create_cpu_job(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    if not state.settings.can_build(principal.role):
        raise ApiError(403, "Sign in to build a CPU.")
    body = await _read_json(request, CreateCpuJobRequest)
    try:
        view = await state.service.cpu_jobs.create(
            body, session_id=principal.sid, role=principal.role, email=principal.email,
        )
    except AdmissionDenied as exc:
        if exc.reason == "no_credits":
            raise ApiError(402, "You have used all your run credits.", "no_credits") from None
        raise
    return _json(view, status=201)


@router.get("/cpu-jobs/mine")
async def my_cpu_jobs(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    jobs = await _state(request).service.cpu_jobs.mine(principal.sid)
    return _json(CpuJobsResponse(jobs=jobs))


@router.get("/cpu-jobs/{job_id}")
async def get_cpu_job(request: Request, job_id: str, principal: Principal = Depends(session_guard)) -> JSONResponse:
    view = await _state(request).service.cpu_jobs.get(
        _valid_run_id(job_id), session_id=principal.sid, role=principal.role, email=principal.email,
    )
    return _json(view)


@router.get("/cpu-jobs/{job_id}/upgrades")
async def get_cpu_upgrades(request: Request, job_id: str, principal: Principal = Depends(session_guard)) -> JSONResponse:
    from ..cpu.advisor import CpuAdvisor

    state = _state(request)
    # The existing job lookup enforces owner or admin before any research or profile disclosure.
    view = await state.service.cpu_jobs.get(
        _valid_run_id(job_id), session_id=principal.sid, role=principal.role, email=principal.email,
    )
    advisor = getattr(request.app.state, "cpu_advisor", None)
    if advisor is None:
        runs = state.service.cpu_jobs.runs
        advisor = CpuAdvisor(state.settings, runs._provider, runs.research,
                             hold=runs._spend_hold, record=runs._record_usd)
        request.app.state.cpu_advisor = advisor
    # Advice is built from CPU v1 (the version an upgrade is compared with). Null until the benchmarks have run.
    version = view.versions[0] if view.versions else None
    blocked = None
    if view.spec_id != "rv32i_micro":
        blocked = "Upgrades start from a passed RV32I core."
    elif view.status != "passed":
        blocked = "The CPU job has not passed yet."
    result = await advisor.advise(
        version.profile if version else None, view.goal, session_id=principal.sid,
        flash_profile=version.flash_profile if version else None, block_reason=blocked,
    )
    return _json(result)


@router.post("/cpu-jobs/{job_id}/upgrade")
async def post_cpu_upgrade(request: Request, job_id: str, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    job_id = _valid_run_id(job_id)
    # The job lookup enforces owner or admin before the body is read.
    await state.service.cpu_jobs.get(job_id, session_id=principal.sid, role=principal.role, email=principal.email)
    if not state.settings.can_build(principal.role):
        raise ApiError(403, "Sign in to build a CPU.")
    body = await _read_json(request, UpgradeRequest)
    try:
        view = await state.service.cpu_jobs.upgrade(
            job_id, body, session_id=principal.sid, role=principal.role, email=principal.email,
        )
    except AdmissionDenied as exc:
        if exc.reason == "no_credits":
            raise ApiError(402, "You have used all your run credits.", "no_credits") from None
        raise
    return _json(view, status=202)


@router.post("/cpu-jobs/{job_id}/retry")
async def post_cpu_retry(request: Request, job_id: str, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    job_id = _valid_run_id(job_id)
    # The job lookup enforces owner or admin before anything else.
    await state.service.cpu_jobs.get(job_id, session_id=principal.sid, role=principal.role, email=principal.email)
    if not state.settings.can_build(principal.role):
        raise ApiError(403, "Sign in to build a CPU.")
    try:
        view = await state.service.cpu_jobs.retry(
            job_id, session_id=principal.sid, role=principal.role, email=principal.email,
        )
    except AdmissionDenied as exc:
        if exc.reason == "no_credits":
            raise ApiError(402, "You have used all your run credits.", "no_credits") from None
        raise
    return _json(view, status=202)


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    state = _state(request)
    settings = state.settings
    try:
        result = await state.service.health()
    except Exception:
        log.exception("health check failed")
        result = HealthResponse(
            live_enabled=False,
            reason="maintenance",
            cap_usd=settings.run_cap_usd,
            provider=settings.provider,
            models=settings.models_by_role(),
        )
    features = [*result.features, "accounts"] if state.auth.supabase_enabled and "accounts" not in result.features else result.features
    return _json(result.model_copy(update={"auth_required": settings.auth.required, "features": features}))


@router.post("/auth/session")
async def create_session(request: Request) -> JSONResponse:
    state = _state(request)
    wait = state.auth_limiter.acquire(client_ip(request))
    if wait > 0:
        raise rate_limited(wait)
    body = await _read_json(request, SessionRequest, limit=1024)
    role = state.auth.check_access_code(body.access_code)
    if role is None:
        raise unauthorized("Invalid access code.")
    token, expires = state.auth.issue_session(role)
    return _json(SessionResponse(token=token, expires_at=iso_z(expires)))


@router.get("/auth/me")
async def auth_me(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    credits = await state.service.get_credits(principal.sid)
    is_super_admin = state.settings.is_super_admin(principal.email, principal.role)
    is_admin = state.settings.is_admin(principal.email, principal.role)
    return _json(
        AuthMeResponse(
            email=principal.email,
            role=principal.role,
            credits=credits,
            is_admin=is_admin,
            is_super_admin=is_super_admin,
            can_build=state.settings.can_build(principal.role),
            anonymous=principal.anonymous,
        )
    )


@router.get("/admin/users")
async def admin_list_users_route(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    if not state.settings.is_admin(principal.email, principal.role):
        raise ApiError(403, "Admin privileges required.")
    raw_users = await state.service.list_user_credits()
    users: list[AdminUserUsage] = []
    for u in raw_users:
        users.append(
            AdminUserUsage(
                owner=u["owner"],
                credits=u["credits"],
                email=u.get("email"),
                role=u.get("role", "user"),
                runs_count=u.get("runs_count", 0),
                total_spent_usd=u.get("total_spent_usd", 0.0),
                created_at=u.get("created_at"),
                updated_at=u.get("updated_at"),
            )
        )
    return _json(AdminListUsersResponse(users=users))


@router.post("/admin/users")
async def admin_create_user_route(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    if not state.settings.is_admin(principal.email, principal.role):
        raise ApiError(403, "Admin privileges required.")
    body = await _read_json(request, AdminCreateUserRequest)
    email = body.email.strip().lower()
    if "@" not in email or len(email) > 254:
        raise ApiError(422, "Please enter a valid email address.")
    if len(body.password) < 6:
        raise ApiError(422, "Password must be at least 6 characters.")
    credits = body.credits if body.credits is not None else state.settings.auth.initial_credits
    try:
        user_id = await state.auth.admin_create_user(email, body.password)
    except AdminCreateUserError as exc:
        raise ApiError(exc.status, exc.message) from None
    owner = f"u:{user_id}"
    await state.service.set_credits(owner, credits, email=email, role="user")
    return _json(
        AdminCreateUserResponse(ok=True, email=email, credits=credits, user_id=user_id),
        status=201,
    )


@router.get("/admin/runs")
async def admin_list_runs_route(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """Every run of one user, newest first, at most 50 (admin only). `owner` is the stored owner string
    (`u:<id>`); `email` is looked up instead when `owner` is absent."""
    state = _state(request)
    if not state.settings.is_admin(principal.email, principal.role):
        raise ApiError(403, "Admin privileges required.")
    owner = request.query_params.get("owner")
    email = request.query_params.get("email")
    if not owner and email:
        clean_email = email.strip().lower()
        all_users = await state.service.list_user_credits()
        for u in all_users:
            if u.get("email") and u["email"].strip().lower() == clean_email:
                owner = u["owner"]
                break
        if not owner:
            owner = f"u:{clean_email}"
    if not owner:
        raise ApiError(422, "Either owner or email must be provided.")
    runs = await state.service.list_runs_for_owner(owner, 50)
    return _json(AdminRunsResponse(owner=owner, email=email, runs=runs))


@router.post("/admin/credits")
async def admin_set_credits_route(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    if not state.settings.is_admin(principal.email, principal.role):
        raise ApiError(403, "Admin privileges required.")
    body = await _read_json(request, AdminSetCreditsRequest)
    target_owner = body.owner
    if not target_owner and body.email:
        clean_email = body.email.strip().lower()
        all_users = await state.service.list_user_credits()
        for u in all_users:
            if u.get("email") and u["email"].strip().lower() == clean_email:
                target_owner = u["owner"]
                break
        if not target_owner:
            target_owner = f"u:{clean_email}"
    if not target_owner:
        raise ApiError(422, "Either owner or email must be provided.")

    current_credits = await state.service.get_credits(target_owner)
    new_credits = body.credits
    if body.add_credits is not None:
        new_credits = current_credits + body.add_credits
    elif new_credits is None:
        raise ApiError(422, "Either credits or add_credits must be specified.")

    await state.service.set_credits(target_owner, new_credits, email=body.email)
    return JSONResponse(
        {"ok": True, "owner": target_owner, "credits": new_credits},
        headers={"Cache-Control": "no-store"},
    )



@router.post("/custom-blocks/approve")
async def approve_custom_block_route(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """CE3: locks a drafted custom block in. The draft was already self-checked by draft_custom_block; this
    re-checks it against exactly what gets written to disk, never trusting a round-tripped payload blindly."""
    state = _state(request)
    body = await _read_json(request, ApproveCustomBlockRequest)
    try:
        pack = await state.service.approve_custom_block(body.draft)
    except ApproveFailed as exc:
        raise ApiError(422, str(exc)) from None
    return _json(ApproveCustomBlockResponse(ok=True, preset_id=pack.id), status=201)


@router.post("/runs")
async def create_run(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    body = await _read_json(request, CreateRunRequest)

    pack = state.service.presets().get(body.preset_id)
    if pack is None:
        raise ApiError(422, "Unknown preset.")
    if body.priority not in pack.priorities:
        raise ApiError(422, "This preset does not support that priority.")
    mode = body.mode or state.settings.limits.default_mode

    wait = state.run_limiter.acquire(principal.sid)
    if wait > 0:
        raise rate_limited(wait)
    try:
        extra: dict[str, str] = {}
        if body.research_id:
            extra["research_id"] = body.research_id
        if body.thread_id:
            extra["thread_id"] = body.thread_id
        run = await state.service.create_run(
            session_id=principal.sid,
            preset_id=body.preset_id,
            priority=body.priority,
            mode=mode,
            params=body.params,
            charge_credits=not state.settings.can_build(principal.role),
            **extra,
        )
    except ParamsError as exc:
        state.run_limiter.refund(principal.sid)
        raise ApiError(422, str(exc)) from exc
    except BaseException:
        state.run_limiter.refund(principal.sid)  # refused before any work started
        raise
    token = state.auth.issue_stream_token(run.id, principal.sid)
    return _json(CreateRunResponse(run_id=run.id, cap_usd=run.budget.max_usd, stream_token=token), status=201)


@router.get("/runs/mine")
async def my_runs(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """The runs this session started (a signed-in user's, on any device), newest first, at most 50."""
    return _json(await _state(request).service.list_my_runs(principal.sid, 50))


@router.post("/runs/{run_id}/stream-token")
async def run_stream_token(run_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """A fresh stream token for a run this session started: the way a signed-in user's other device or tab (which never
    saw the 201 of POST /runs) opens the live stream of a run that is still going."""
    state = _state(request)
    run = await _owned_run(state, run_id, principal.sid)
    return JSONResponse(
        {"streamToken": state.auth.issue_stream_token(run.id, principal.sid)}, headers={"Cache-Control": "no-store"}
    )


@router.post("/runs/{run_id}/resume")
async def resume_run_route(run_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """Resume an active run stopped at a checkpoint (e.g. credits exhausted)."""
    state = _state(request)
    rid = _valid_run_id(run_id)
    try:
        record = await state.service.resume_run(
            rid, session_id=principal.sid, charge_credits=not state.settings.can_build(principal.role)
        )
    except (NotFound, Forbidden, Conflict, AdmissionDenied):
        raise
    token = state.auth.issue_stream_token(record.id, principal.sid)
    return _json(ResumeRunResponse(ok=True, run_id=record.id, status=record.status, stream_token=token))


@router.get("/runs")
async def list_runs(request: Request, limit: int = 50, offset: int = 0) -> JSONResponse:
    state = _state(request)
    clamped_limit = max(1, min(limit, 100))
    clamped_offset = max(0, offset)
    result = await state.service.list_runs(limit=clamped_limit, offset=clamped_offset)
    return _json(result)


@router.post("/parse")
async def parse_text(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    body = await _read_json(request, ParseRequest)
    wait = state.parse_limiter.acquire(principal.sid)
    if wait > 0:
        raise rate_limited(wait)
    try:
        reply = await state.service.parse(body.text, session_id=principal.sid)
    except AdmissionDenied as exc:
        state.parse_limiter.refund(principal.sid)
        if exc.reason == "maintenance":
            raise ApiError(503, "Live runs are offline for maintenance.", "maintenance") from None
        raise  # budget_exhausted: the shared handler answers 429 with the reason
    except LLMError:
        log.warning("parse: the model call failed", exc_info=True)
        raise ApiError(502, "The model could not be reached. Try again shortly.") from None
    return _json(reply)


@router.post("/runs/{run_id}/ask")
async def ask_run(run_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """A question or a change request about an ended run, from its owner session (plan claim 3)."""
    state = _state(request)
    run = await _owned_run(state, run_id, principal.sid)
    if run.status in ACTIVE_STATUSES:
        raise ApiError(409, "The run is still going. Ask again when it ends.")
    body = await _read_json(request, AskRequest)
    wait = state.ask_limiter.acquire(principal.sid)
    if wait > 0:
        raise rate_limited(wait)
    try:
        reply = await state.service.ask(
            run_id, session_id=principal.sid, text=body.text, focus_version=body.focus_version
        )
    except AdmissionDenied as exc:
        state.ask_limiter.refund(principal.sid)
        if exc.reason == "maintenance":
            raise ApiError(503, "Live runs are offline for maintenance.", "maintenance") from None
        raise  # budget_exhausted: the shared handler answers 429 with the reason
    except BaseException:
        state.ask_limiter.refund(principal.sid)  # 404, 403 or 409 from the service: nothing was answered
        raise
    return _json(reply)


async def _on_screen_cpu_job(state: ApiState, job_id: str | None, principal: Principal) -> str | None:
    """The CPU job id a chat message says is on screen, kept only when it passes the rule of GET /cpu-jobs/{id} (owner,
    or an admin). A bad format, an unknown job or another account's job is dropped: a stale link never blocks chatting."""
    if not job_id or not _CPU_JOB_ID_RE.fullmatch(job_id):
        return None
    try:
        await state.service.cpu_jobs.get(job_id, session_id=principal.sid, role=principal.role, email=principal.email)
    except (NotFound, Forbidden):
        return None
    except Exception:
        log.warning("thread message: could not check the on-screen CPU job", exc_info=True)
        return None
    return job_id


@router.post("/threads/{thread_id}/messages")
async def thread_message(thread_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """One message of a thread (backend ARCHITECTURE 17.4): the fact check on the first chip request, then the agent."""
    state = _state(request)
    thread_id = _valid_thread_id(thread_id)
    body = await _read_json(request, ThreadMessageRequest)
    if body.run_id:
        run = await _owned_run(state, body.run_id, principal.sid)  # 404 or 403 for a run that is not this session's
        if run.status in ACTIVE_STATUSES:
            raise ApiError(409, "The run is still going. Ask again when it ends.")  # like POST /runs/{id}/ask
    wait = state.message_limiter.acquire(principal.sid)
    if wait > 0:
        raise rate_limited(wait)
    try:
        # after the bucket: it never refuses, and a message the bucket refused should not read the job
        cpu_job_id = await _on_screen_cpu_job(state, body.cpu_job_id, principal)
        reply = await state.service.thread_message(
            thread_id, session_id=principal.sid, text=body.text, run_id=body.run_id, focus_version=body.focus_version,
            cpu_job_id=cpu_job_id, can_build_cpu=state.settings.can_build(principal.role),
            cpu_admin=state.settings.is_admin(principal.email, principal.role),
        )
    except AdmissionDenied as exc:
        state.message_limiter.refund(principal.sid)
        if exc.reason == "maintenance":
            raise ApiError(503, "Live runs are offline for maintenance.", "maintenance") from None
        raise  # budget_exhausted: the shared handler answers 429 with the reason
    except LLMError:
        state.message_limiter.refund(principal.sid)
        log.warning("thread message: the fact check's model call failed", exc_info=True)
        raise ApiError(502, "The model could not be reached. Try again shortly.") from None
    except BaseException:
        state.message_limiter.refund(principal.sid)
        raise
    return _json(reply)


@router.get("/threads")
async def list_threads(request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """This session's threads, newest first (at most 50)."""
    return _json(await _state(request).service.list_threads(principal.sid, 50))


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    """The stored turns of one of this session's threads; another session's thread is a 404."""
    thread_id = _valid_thread_id(thread_id)
    try:
        view = await _state(request).service.read_thread(principal.sid, thread_id)
    except NotFound:
        raise ApiError(404, "Thread not found.") from None
    return _json(view)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> Response:
    state = _state(request)
    _valid_run_id(run_id)

    stream_token = request.query_params.get("st")
    claims = state.auth.read_stream_token(stream_token) if stream_token else None
    if claims is not None and claims[0] == run_id:
        _charge_session(state, claims[1])
        run = await _owned_run(state, run_id, claims[1])
    else:
        found = await state.service.get_run(run_id)
        if found is not None and found.status not in ACTIVE_STATUSES:
            # A finished run replays on the same public rule as GET /runs/{id}/log, so a reload with an expired
            # stream token (auth.stream_ttl_s) still shows it. A running run's live stream always needs a valid token
            # (or, with auth off, the owner's anonymous session).
            principal = await _principal_opt(request, state)
            if principal is not None:
                _charge_session(state, principal.sid)
            run = found
        elif stream_token:
            raise unauthorized("Invalid or expired stream token.")
        elif state.settings.auth.required:
            raise unauthorized("A stream token is required.")
        else:
            sid = (await _principal(request, state)).sid
            _charge_session(state, sid)
            run = await _owned_run(state, run_id, sid)

    after = _after_seq(request)
    stream = state.service.stream(run_id, after)
    agen = stream.__aiter__()

    first = None
    if run.status not in ACTIVE_STATUSES:
        # UNVERIFIED: that the browser EventSource stops reconnecting on HTTP 204 (spec says it does) and that
        # frontend/src/lib/sources/live.ts treats the resulting error with readyState CLOSED as a normal end.
        # A finished run replays and ends. When there is nothing left after `after`, answer 204: EventSource
        # treats 204 as "do not reconnect", so a client that already has the terminal event stops cleanly.
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_PEEK_TIMEOUT_S)
        except StopAsyncIteration:
            await close_stream(agen)
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        except Exception:
            await close_stream(agen)
            raise ApiError(503, "The event stream is unavailable.") from None

    return StreamingResponse(
        sse_body(agen, float(state.settings.sse.ping_s), first, FINISHED_RETRY_MS if first is not None else RETRY_MS),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _after_seq(request: Request) -> int:
    """Resume point: Last-Event-ID header (sent by EventSource on reconnect), else ?after=. Junk means 0."""
    raw = request.headers.get("last-event-id") or request.query_params.get("after") or "0"
    raw = raw.strip()
    return int(raw) if raw.isdigit() and len(raw) <= 12 else 0


@router.get("/runs/{run_id}/log")
async def run_log(run_id: str, request: Request) -> Response:
    state = _state(request)
    _valid_run_id(run_id)
    run = await state.service.get_run(run_id)
    if run is None:
        raise ApiError(404, "Run not found.")
    if run.status in ACTIVE_STATUSES:
        principal = await _principal_opt(request, state)
        if principal is None:
            raise unauthorized("Missing or invalid session token.")
        _charge_session(state, principal.sid)
        if run.session_id != principal.sid:
            raise ApiError(403, "This run belongs to another session.")
    else:
        # Finished runs are publicly readable without ownership.
        principal = await _principal_opt(request, state)
        if principal is not None:
            _charge_session(state, principal.sid)
    log_model = await state.service.run_log(run_id)
    return Response(content=to_json(log_model), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/runs/{run_id}/steer")
async def steer_run(run_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    await _owned_run(state, run_id, principal.sid)
    raw = await _read_raw(request, MAX_STEER_BYTES)
    try:
        steer = STEER_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise ApiError(422, validation_summary([e["loc"] for e in exc.errors()])) from None
    await state.service.steer(run_id, principal.sid, steer)
    return JSONResponse({"accepted": True}, status_code=202)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request, principal: Principal = Depends(session_guard)) -> JSONResponse:
    state = _state(request)
    await _owned_run(state, run_id, principal.sid)
    await state.service.stop(run_id, principal.sid)
    return JSONResponse({"accepted": True}, status_code=202)


def new_limiters(settings: Settings) -> tuple[TokenBucketLimiter, TokenBucketLimiter, TokenBucketLimiter, TokenBucketLimiter]:
    """(per-IP requests, per-session requests, auth attempts per IP, new runs per session)."""
    limits = settings.limits
    return (
        TokenBucketLimiter(limits.api_per_session_per_min, 60.0),
        TokenBucketLimiter(limits.api_per_session_per_min, 60.0),
        TokenBucketLimiter(limits.auth_per_ip, float(limits.auth_window_s)),
        TokenBucketLimiter(limits.runs_per_session_per_hour, 3600.0),
    )


__all__ = ["ApiState", "ip_guard", "new_limiters", "router"]
