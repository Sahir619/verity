"""Single-login auth (ARCHITECTURE 12.1, CONTRACTS section 9).

    POST /auth/session {accessCode}  -> constant-time compare against sha256 hashes from env
                                     -> HMAC-signed session token {sid, role}, max age auth.session_ttl_s (12 h)
    REST:  Authorization: Bearer <session token>
    SSE:   ?st=<stream token>, signed {runId, sid}, max age auth.stream_ttl_s (30 min), one run, read-only

A signed-in user sends a Supabase access token instead (Authorization: Bearer, same header). The Verity token is tried
first, then the Supabase token when SUPABASE_URL is set. The token is checked here, never trusted:

    keys    the project's JWKS (auth/v1/.well-known/jwks.json), kept at most supabase.jwks_cache_s (10 minutes). The
            algorithm is the matched key's own (ES256 for an EC P-256 key, RS256 for an RSA key): the token header can
            only agree with it, so HS256 and "none" tokens are refused.
    claims  iss = <url>/auth/v1, aud = "authenticated", exp (10 s leeway), sub is a uuid.
    legacy  a project that still signs with the shared secret has no keys in its JWKS: GET auth/v1/user with the
            publishable key decides, and the answer is kept 60 s per token.
    user    sid "u:" + the user id, role "user": the same on every device, so rate limits, spend limits, run ownership,
            stream tokens and threads follow the user.

When auth.required is false (development, and until frontend/ sends tokens) a request with no valid token is an
anonymous session whose id is "anon-" + sha256(client ip)[:16].

Signing secret: VERITY_SIGNING_SECRET when set. Outside production, when it is not set, a random secret is created
once in settings.data_dir (SECRET_FILE) and read on later starts, so session and stream tokens survive a restart.
Production refuses to start without VERITY_SIGNING_SECRET (Settings.production_problems). The secret is never logged.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
from itsdangerous import BadData, URLSafeTimedSerializer

from ..config import Settings

log = logging.getLogger("verity.api")

SESSION_SALT = "verity.session"
STREAM_SALT = "verity.stream"
MAX_ACCESS_CODE_CHARS = 256
SECRET_FILE = "signing-secret"  # under settings.data_dir (backend/data/, gitignored)
MIN_SECRET_CHARS = 32

MAX_SUPABASE_TOKEN_CHARS = 4_096
MAX_JWKS_BYTES = 65_536
SUPABASE_AUDIENCE = "authenticated"
SUPABASE_LEEWAY_S = 10
JWKS_REFRESH_ON_UNKNOWN_KID_S = 30  # a token with a kid we do not know re-reads the keys, at most this often
LEGACY_TTL_S = 60.0  # the answer of auth/v1/user is kept this long per token
LEGACY_CACHE_MAX = 256


@dataclass(frozen=True, slots=True)
class Principal:
    sid: str
    role: str
    anonymous: bool = False
    email: str | None = None  # a signed-in user's email, when the token has one


def anonymous_sid(client_ip: str) -> str:
    return "anon-" + hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class _SigningKey:
    alg: str  # the only algorithm this key may verify
    key: Any  # a public key object from the cryptography package


@dataclass(frozen=True, slots=True)
class _Jwks:
    keys: dict[str, _SigningKey]  # by kid
    legacy: bool  # the project publishes no keys: it still signs with the shared secret
    fetched: float  # time.monotonic() of the read


class _KeysUnavailable(Exception):
    """The project's signing keys could not be read (network error, bad status or body)."""


class AdminCreateUserError(Exception):
    """Raised when admin creation of a user fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)



def _jwk_algorithm(jwk: dict[str, Any]) -> str | None:
    """ES256 for an EC P-256 key, RS256 for an RSA key; None for anything else (or a key that names another algorithm)."""
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        wanted = "ES256"
    elif jwk.get("kty") == "RSA":
        wanted = "RS256"
    else:
        return None
    return wanted if jwk.get("alg") in (None, wanted) and jwk.get("use") in (None, "sig") else None


def _parse_jwks(document: dict[str, Any], fetched: float) -> _Jwks:
    raw = document.get("keys")
    raw = raw if isinstance(raw, list) else []
    keys: dict[str, _SigningKey] = {}
    for jwk in raw:
        if not isinstance(jwk, dict) or not isinstance(jwk.get("kid"), str):
            continue
        alg = _jwk_algorithm(jwk)
        if alg is None:
            continue
        try:
            keys[jwk["kid"]] = _SigningKey(alg, jwt.PyJWK.from_dict(jwk, algorithm=alg).key)
        except (jwt.PyJWTError, ValueError, TypeError):
            continue
    return _Jwks(keys=keys, legacy=not raw, fetched=fetched)


def _user_principal(user_id: object, email: object) -> Principal | None:
    """A signed-in user's Principal: sid "u:" + the user id (a uuid), role "user"."""
    if not isinstance(user_id, str):
        return None
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    mail = email.strip() if isinstance(email, str) and "@" in email and len(email) <= 254 else None
    return Principal(sid=f"u:{uid}", role="user", email=mail)


class AuthService:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http
        self._jwks: _Jwks | None = None
        self._jwks_lock = asyncio.Lock()
        self._legacy: dict[str, tuple[float, Principal]] = {}  # sha256(token) -> (valid until, principal)
        configured = settings.signing_secret.get_secret_value() if settings.signing_secret else ""
        stored = None if configured or settings.env == "production" else _stored_secret(settings.data_dir / SECRET_FILE)
        if configured:
            secret = configured
        elif stored:
            secret = stored
            log.info("VERITY_SIGNING_SECRET is not set: using the development secret kept in %s", settings.data_dir)
        else:
            secret = secrets.token_urlsafe(48)
            log.warning("VERITY_SIGNING_SECRET is not set: using a random secret, tokens will not survive a restart")
        self._session = URLSafeTimedSerializer(secret, salt=SESSION_SALT)
        self._stream = URLSafeTimedSerializer(secret, salt=STREAM_SALT)
        self._codes = settings.access_codes  # [(role, sha256 hex)]

    @property
    def required(self) -> bool:
        return self._settings.auth.required

    @property
    def supabase_enabled(self) -> bool:
        return self._settings.supabase_auth_enabled()

    async def aclose(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()

    # ---------- access codes ----------
    def check_access_code(self, code: str) -> str | None:
        """Role for a valid access code, else None. Compares against every stored hash (no early exit), and
        compare_digest is constant-time per comparison."""
        digest = hashlib.sha256(code[:MAX_ACCESS_CODE_CHARS].encode("utf-8")).hexdigest().encode("ascii")
        matched: str | None = None
        for role, stored in self._codes:
            if hmac.compare_digest(digest, stored.encode("utf-8")) and matched is None:
                matched = role
        return matched

    # ---------- session tokens ----------
    def issue_session(self, role: str) -> tuple[str, datetime]:
        sid = "s_" + secrets.token_hex(12)
        token = self._session.dumps({"sid": sid, "role": role})
        expires = datetime.now(UTC) + timedelta(seconds=self._settings.auth.session_ttl_s)
        return token, expires

    def read_session(self, token: str) -> Principal | None:
        try:
            data = self._session.loads(token, max_age=self._settings.auth.session_ttl_s)
        except BadData:
            return None
        if not isinstance(data, dict):
            return None
        sid, role = data.get("sid"), data.get("role")
        if not isinstance(sid, str) or not sid or not isinstance(role, str):
            return None
        return Principal(sid=sid, role=role)

    async def authenticate(self, token: str) -> Principal | None:
        """The Principal for a bearer token: a Verity session token first, then a Supabase access token when sign-in is
        configured. None when neither is valid."""
        principal = self.read_session(token)
        if principal is None and self.supabase_enabled:
            principal = await self.verify_supabase_token(token)
        return principal

    # ---------- Supabase access tokens ----------
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._settings.supabase.timeout_s, follow_redirects=False)
        return self._http

    def _supabase_url(self, path: str) -> str:
        return (self._settings.supabase.url or "").strip().rstrip("/") + path

    async def _fetch_jwks(self) -> dict[str, Any]:
        """The project's JWKS document. Raises _KeysUnavailable; only the error type is logged."""
        try:
            response = await self._client().get(self._supabase_url("/auth/v1/.well-known/jwks.json"))
            response.raise_for_status()
            if len(response.content) > MAX_JWKS_BYTES:
                raise ValueError("too large")
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Supabase signing keys could not be read: %s", type(exc).__name__)
            raise _KeysUnavailable from None
        if not isinstance(document, dict):
            raise _KeysUnavailable
        return document

    async def _signing_keys(self, max_age_s: float) -> _Jwks:
        """The cached keys when they are younger than max_age_s, else a fresh read (one at a time)."""
        current = self._jwks
        if current is not None and time.monotonic() - current.fetched < max_age_s:
            return current
        async with self._jwks_lock:
            current = self._jwks
            if current is not None and time.monotonic() - current.fetched < max_age_s:
                return current
            self._jwks = _parse_jwks(await self._fetch_jwks(), time.monotonic())
            return self._jwks

    async def verify_supabase_token(self, token: str) -> Principal | None:
        """The signed-in user for a Supabase access token, or None for anything that is not a valid one. Never raises."""
        if not self.supabase_enabled or not token or len(token) > MAX_SUPABASE_TOKEN_CHARS:
            return None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        if not isinstance(header.get("alg"), str):
            return None
        cache_s = float(self._settings.supabase.jwks_cache_s)
        kid = header.get("kid")
        try:
            jwks = await self._signing_keys(cache_s)
            if jwks.legacy:
                return await self._verify_with_project(token)
            if not isinstance(kid, str):
                return None
            if kid not in jwks.keys:
                # A key the project added after our last read: look again, but not for every junk token.
                jwks = await self._signing_keys(min(cache_s, JWKS_REFRESH_ON_UNKNOWN_KID_S))
        except _KeysUnavailable:
            return None
        signing = jwks.keys.get(kid)
        if signing is None or header["alg"] != signing.alg:
            return None  # the algorithm comes from the key, never from the token
        try:
            claims = jwt.decode(
                token,
                signing.key,
                algorithms=[signing.alg],
                audience=SUPABASE_AUDIENCE,
                issuer=self._supabase_url("/auth/v1"),
                leeway=SUPABASE_LEEWAY_S,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return None
        if claims.get("is_anonymous") is True:
            return None  # an anonymous sign-in is not one of the accounts the team made
        return _user_principal(claims.get("sub"), claims.get("email"))

    async def _verify_with_project(self, token: str) -> Principal | None:
        """Legacy projects (shared secret): ask auth/v1/user. 200 means valid; the answer is kept LEGACY_TTL_S per token."""
        try:
            exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        except jwt.PyJWTError:
            return None
        if not isinstance(exp, (int, float)) or exp < time.time() - SUPABASE_LEEWAY_S:
            return None  # junk or expired: no call to Supabase
        publishable = self._settings.supabase.publishable_key
        if publishable is None or not publishable.get_secret_value():
            log.warning("Supabase has no signing keys and SUPABASE_PUBLISHABLE_KEY is not set: sign-in tokens cannot be checked")
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        hit = self._legacy.get(digest)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        try:
            response = await self._client().get(
                self._supabase_url("/auth/v1/user"),
                headers={"apikey": publishable.get_secret_value(), "Authorization": f"Bearer {token}"},
            )
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Supabase user check failed: %s", type(exc).__name__)
            return None
        principal = _user_principal(body.get("id"), body.get("email")) if isinstance(body, dict) and body.get("is_anonymous") is not True else None
        if principal is not None:
            now = time.monotonic()
            if len(self._legacy) >= LEGACY_CACHE_MAX:
                self._legacy = {k: v for k, v in self._legacy.items() if v[0] > now}
                if len(self._legacy) >= LEGACY_CACHE_MAX:
                    self._legacy.clear()
            self._legacy[digest] = (now + LEGACY_TTL_S, principal)
        return principal

    # ---------- Admin user management ----------
    async def admin_create_user(self, email: str, password: str) -> str:
        """Create a user in Supabase auth with email and password, auto-confirmed (no verification email).
        Returns the user uuid string."""
        if not self.supabase_enabled or not self._settings.has_supabase_secret():
            # In development or without Supabase, return a deterministic UUID
            uid = hashlib.sha256(f"local:{email}".encode("utf-8")).hexdigest()[:32]
            return f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"

        secret = self._settings.supabase.secret_key.get_secret_value()  # type: ignore[union-attr]
        try:
            response = await self._client().post(
                self._supabase_url("/auth/v1/admin/users"),
                headers={
                    "apikey": secret,
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("Supabase admin user creation network failure: %s", type(exc).__name__)
            raise AdminCreateUserError(502, "The sign-in service could not be reached. Try again shortly.") from None

        if response.status_code in (200, 201):
            try:
                body = response.json()
            except ValueError:
                raise AdminCreateUserError(502, "Invalid response from the sign-in service.") from None
            uid = body.get("id") if isinstance(body, dict) else None
            if not isinstance(uid, str) or not uid:
                raise AdminCreateUserError(502, "Sign-in service returned no user id.")
            return uid

        if response.status_code == 422:
            raise AdminCreateUserError(422, "An account with that email already exists.")

        log.warning("Supabase admin user creation returned status %s", response.status_code)
        raise AdminCreateUserError(response.status_code, "Could not create user account.")


    # ---------- stream tokens ----------
    def issue_stream_token(self, run_id: str, sid: str) -> str:
        return self._stream.dumps({"runId": run_id, "sid": sid})

    def read_stream_token(self, token: str) -> tuple[str, str] | None:
        """(run_id, sid) for a valid, unexpired stream token."""
        try:
            data = self._stream.loads(token, max_age=self._settings.auth.stream_ttl_s)
        except BadData:
            return None
        if not isinstance(data, dict):
            return None
        run_id, sid = data.get("runId"), data.get("sid")
        if not isinstance(run_id, str) or not isinstance(sid, str):
            return None
        return run_id, sid


def _stored_secret(path: Path) -> str | None:
    """The development signing secret kept at `path`: read it, or create it once (owner read and write only where
    the OS supports it). None when the file cannot be read or written; the caller then uses a per-process secret."""
    try:
        current = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        current = ""
    except (OSError, UnicodeDecodeError):
        log.warning("could not read the stored signing secret in %s", path.parent)
        return None
    if len(current) >= MIN_SECRET_CHARS:
        return current

    secret = secrets.token_urlsafe(48)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secret)
        os.replace(tmp, path)  # atomic: a later start never reads a half-written secret
    except OSError:
        log.warning("could not store a signing secret in %s", path.parent)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    return secret


def iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
