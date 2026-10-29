"""Sign-in with Supabase access tokens: verification of the token, the user's session id, and what follows from it.

No Supabase project and no network. The project's signing key is a P-256 key made here; its JWKS reaches the server through
a monkeypatched fetch (and, in two tests, through httpx.MockTransport so the real request is seen). Tokens the server must
refuse: wrong issuer, wrong audience, expired, bad signature, unknown kid, HS256 signed with the public key bytes, alg none.
"""

import base64
import copy
import dataclasses
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from test_sse_and_routes import code_hash
from verity import main as main_module
from verity.api import auth as auth_module
from verity.api.app import create_app
from verity.api.auth import AuthService, Principal
from verity.api.redact import redact
from verity.config import AuthConfig, SupabaseConfig, load_settings
from verity.llm.fake import FakeProvider
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity.store.supabase_threads import SupabaseThreadStore
from verity.tools.local_docker import LocalDockerRunner
from verity_test_support import FakeToolRunner, make_settings, tmp_cassettes

URL = "https://abcdefghijkl.supabase.co"
ISS = f"{URL}/auth/v1"
PUBLISHABLE = "sb_publishable_test_key"
KID = "key-1"
USER_A = "0b6f6a52-7a47-4d6a-9d0f-1c6f3f5e8a01"
USER_B = "7d1c9d57-2c1b-4c0e-8f5b-3a9a4f0b6c02"
THREAD = "11111111-2222-4333-8444-555555555555"


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


KEY = new_key()
OTHER_KEY = new_key()
PUBLIC_PEM = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def jwk_of(key: ec.EllipticCurvePrivateKey, kid: str = KID) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    return {**jwk, "kid": kid, "alg": "ES256", "use": "sig"}


def make_token(key: Any = KEY, *, kid: str | None = KID, sub: str | None = USER_A, iss: str = ISS, aud: Any = "authenticated",
               exp_in: int | None = 3600, email: str | None = "judge@example.com", alg: str = "ES256", **extra: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {"iss": iss, "aud": aud, "iat": now - 5, "jti": uuid.uuid4().hex, "role": "authenticated", **extra}
    if exp_in is not None:
        payload["exp"] = now + exp_in
    if sub is not None:
        payload["sub"] = sub
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid} if kid else None)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hand_made(alg: str, secret: bytes | None) -> str:
    """A token built by hand: HS256 signed with `secret`, or alg none with no signature."""
    header = b64(json.dumps({"alg": alg, "typ": "JWT", "kid": KID}).encode())
    claims = {"sub": USER_A, "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 3600, "email": "judge@example.com"}
    body = f"{header}.{b64(json.dumps(claims).encode())}"
    signature = b64(hmac.new(secret, body.encode(), hashlib.sha256).digest()) if secret is not None else ""
    return f"{body}.{signature}"


REFUSED = {
    "wrong issuer": lambda: make_token(iss="https://evil.supabase.co/auth/v1"),
    "wrong audience": lambda: make_token(aud="anon"),
    "expired": lambda: make_token(exp_in=-3600),
    "no expiry": lambda: make_token(exp_in=None),
    "bad signature": lambda: make_token(OTHER_KEY),
    "unknown kid": lambda: make_token(kid="not-a-known-key"),
    "no kid": lambda: make_token(kid=None),
    "HS256 signed with the public key": lambda: hand_made("HS256", PUBLIC_PEM),
    "alg none": lambda: hand_made("none", None),
    "anonymous sign-in": lambda: make_token(email=None, is_anonymous=True),
    "sub is not a uuid": lambda: make_token(sub="not-a-uuid"),
    "no sub": lambda: make_token(sub=None),
    "junk": lambda: "not.a.jwt",
    "empty": lambda: "x",
}


@pytest.fixture()
def jwks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The project's key set, fed to the server through a patched fetch. `fetches` counts the reads."""
    state = SimpleNamespace(doc={"keys": [jwk_of(KEY)]}, fetches=0, fail=False)

    async def fake_fetch(self: AuthService) -> dict[str, Any]:
        state.fetches += 1
        if state.fail:
            raise auth_module._KeysUnavailable
        return copy.deepcopy(state.doc)

    monkeypatch.setattr(AuthService, "_fetch_jwks", fake_fetch)
    return state


def settings_for(tmp_path, *, required: bool = True, **supabase):  # noqa: ANN003, ANN201
    cfg = {"url": URL, "publishable_key": SecretStr(PUBLISHABLE), **supabase}
    return make_settings(tmp_path / "data").model_copy(
        update={
            "auth": AuthConfig(required=required),
            "signing_secret": SecretStr("k" * 40),
            "access_code_hashes": SecretStr(f"team:{code_hash('letmein')}"),
            "supabase": SupabaseConfig(**cfg),
        }
    )


def app_client(tmp_path, packs, **kw):  # noqa: ANN003, ANN201
    settings = settings_for(tmp_path, **kw)
    provider = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    store = SqliteStore(settings)
    manager = RunManager(settings, store, provider, FakeToolRunner(delay_s=0.2), packs, threads=store)
    return TestClient(create_app(settings, manager, startup=[store.open], shutdown=[store.close]))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


RUN = {"presetId": "add8", "priority": "speed", "mode": "fast", "params": {"width": 16}}


# ---------- verifying a token ----------
async def test_a_valid_token_is_a_user_whose_session_id_is_u_and_the_user_id(tmp_path, jwks) -> None:
    auth = AuthService(settings_for(tmp_path))
    principal = await auth.verify_supabase_token(make_token())
    assert principal == Principal(sid=f"u:{USER_A}", role="user", anonymous=False, email="judge@example.com")
    assert (await auth.authenticate(make_token(sub=USER_A.upper(), email=None))) == Principal(sid=f"u:{USER_A}", role="user")


async def test_a_token_a_few_seconds_past_its_expiry_is_still_accepted_but_not_a_minute(tmp_path, jwks) -> None:
    auth = AuthService(settings_for(tmp_path))
    assert await auth.verify_supabase_token(make_token(exp_in=-5)) is not None  # the leeway is 10 s
    assert await auth.verify_supabase_token(make_token(exp_in=-60)) is None


@pytest.mark.parametrize("name", list(REFUSED))
async def test_a_token_that_is_not_valid_is_refused(tmp_path, jwks, name) -> None:
    auth = AuthService(settings_for(tmp_path))
    assert await auth.verify_supabase_token(REFUSED[name]()) is None
    assert await auth.authenticate(REFUSED[name]()) is None


async def test_a_token_over_the_size_limit_is_refused_without_a_key_read(tmp_path, jwks) -> None:
    auth = AuthService(settings_for(tmp_path))
    assert await auth.verify_supabase_token(make_token() + "A" * 5_000) is None
    assert jwks.fetches == 0


async def test_nothing_is_accepted_when_the_project_is_not_configured(tmp_path, jwks) -> None:
    auth = AuthService(make_settings(tmp_path / "data"))
    assert not auth.supabase_enabled
    assert await auth.authenticate(make_token()) is None and jwks.fetches == 0


async def test_a_verity_session_token_still_works_and_costs_no_key_read(tmp_path, jwks) -> None:
    auth = AuthService(settings_for(tmp_path))
    token, _ = auth.issue_session("team")
    principal = await auth.authenticate(token)
    assert principal is not None and principal.role == "team" and principal.sid.startswith("s_") and jwks.fetches == 0


# ---------- the signing keys ----------
async def test_the_keys_are_read_once_and_again_after_the_cache_time(tmp_path, jwks) -> None:
    auth = AuthService(settings_for(tmp_path))
    for _ in range(3):
        assert await auth.verify_supabase_token(make_token()) is not None
    assert jwks.fetches == 1
    auth._jwks = dataclasses.replace(auth._jwks, fetched=auth._jwks.fetched - 601)  # older than 10 minutes
    assert await auth.verify_supabase_token(make_token()) is not None
    assert jwks.fetches == 2


def test_the_key_cache_can_never_be_set_above_ten_minutes() -> None:
    with pytest.raises(ValidationError):
        SupabaseConfig(jwks_cache_s=601)
    assert SupabaseConfig().jwks_cache_s == 600


async def test_a_key_added_after_the_last_read_is_found_but_junk_does_not_cause_a_read_each_time(tmp_path, jwks) -> None:
    other = new_key()
    auth = AuthService(settings_for(tmp_path))
    assert await auth.verify_supabase_token(make_token()) is not None
    jwks.doc["keys"].append(jwk_of(other, "key-2"))  # the project rotated
    fresh = make_token(other, kid="key-2")
    assert await auth.verify_supabase_token(fresh) is None and jwks.fetches == 1  # the keys are only seconds old
    assert await auth.verify_supabase_token(make_token(kid="junk")) is None and jwks.fetches == 1
    auth._jwks = dataclasses.replace(auth._jwks, fetched=auth._jwks.fetched - 31)
    assert await auth.verify_supabase_token(fresh) is not None and jwks.fetches == 2


async def test_unreadable_keys_mean_no_sign_in_not_a_crash(tmp_path, jwks) -> None:
    jwks.fail = True
    assert await AuthService(settings_for(tmp_path)).verify_supabase_token(make_token()) is None


async def test_only_ec_p256_and_rsa_keys_are_used_and_each_keeps_its_own_algorithm(tmp_path, jwks) -> None:
    jwks.doc = {"keys": [{**jwk_of(KEY), "alg": "HS256"}, {"kty": "oct", "kid": KID, "k": b64(b"secret")}, "junk"]}
    auth = AuthService(settings_for(tmp_path))
    assert await auth.verify_supabase_token(make_token()) is None  # no usable key, and not a legacy project either
    assert auth._jwks.legacy is False and auth._jwks.keys == {}


async def test_the_real_request_for_the_keys_goes_to_the_well_known_url_with_no_credentials(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"keys": [jwk_of(KEY)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        auth = AuthService(settings_for(tmp_path), http=http)
        assert await auth.verify_supabase_token(make_token()) is not None
    assert [str(r.url) for r in seen] == [f"{URL}/auth/v1/.well-known/jwks.json"]
    assert "authorization" not in seen[0].headers and "apikey" not in seen[0].headers


@pytest.mark.parametrize("answer", [httpx.Response(500), httpx.Response(200, content=b"<html>"), httpx.Response(200, json=["keys"])])
async def test_a_bad_answer_for_the_keys_is_no_sign_in(tmp_path, answer) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: answer)) as http:
        assert await AuthService(settings_for(tmp_path), http=http).verify_supabase_token(make_token()) is None


# ---------- a project that still signs with the shared secret ----------
SHARED = "a-shared-secret-of-the-legacy-project"


def legacy_token(**kw: Any) -> str:
    return make_token(SHARED, kid=None, alg="HS256", **kw)


class LegacyProject:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body if body is not None else {"id": USER_A, "email": "judge@example.com", "aud": "authenticated"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


async def legacy_auth(tmp_path, jwks, project: LegacyProject, **supabase):  # noqa: ANN001, ANN003, ANN201
    jwks.doc = {"keys": []}  # a legacy project publishes no keys
    http = httpx.AsyncClient(transport=httpx.MockTransport(project))
    return AuthService(settings_for(tmp_path, **supabase), http=http), http


async def test_with_no_published_keys_the_project_decides_and_the_answer_is_kept_60_seconds(tmp_path, jwks) -> None:
    project = LegacyProject()
    auth, http = await legacy_auth(tmp_path, jwks, project)
    token = legacy_token()
    expected = Principal(sid=f"u:{USER_A}", role="user", email="judge@example.com")
    assert await auth.verify_supabase_token(token) == expected
    [request] = project.requests
    assert str(request.url) == f"{URL}/auth/v1/user"
    assert request.headers["apikey"] == PUBLISHABLE and request.headers["authorization"] == f"Bearer {token}"
    assert await auth.verify_supabase_token(token) == expected and len(project.requests) == 1  # kept
    auth._legacy = {k: (0.0, p) for k, (_, p) in auth._legacy.items()}  # 60 s later
    assert await auth.verify_supabase_token(token) == expected and len(project.requests) == 2
    await http.aclose()


async def test_the_legacy_check_takes_the_user_from_the_answer_not_from_the_token(tmp_path, jwks) -> None:
    project = LegacyProject(body={"id": USER_B, "email": "other@example.com"})
    auth, http = await legacy_auth(tmp_path, jwks, project)
    principal = await auth.verify_supabase_token(legacy_token(sub=USER_A))
    assert principal is not None and principal.sid == f"u:{USER_B}"
    await http.aclose()


@pytest.mark.parametrize("project", [LegacyProject(401, {"msg": "invalid JWT"}), LegacyProject(200, {"id": "nope"}), LegacyProject(200, ["x"]), LegacyProject(200, {"id": USER_A, "is_anonymous": True})])
async def test_the_legacy_check_refuses_what_the_project_refuses_and_does_not_keep_it(tmp_path, jwks, project) -> None:
    auth, http = await legacy_auth(tmp_path, jwks, project)
    token = legacy_token()
    assert await auth.verify_supabase_token(token) is None and await auth.verify_supabase_token(token) is None
    assert len(project.requests) == 2  # a refusal is asked again, not remembered
    await http.aclose()


async def test_the_legacy_check_makes_no_call_for_junk_expired_tokens_or_a_missing_key(tmp_path, jwks) -> None:
    project = LegacyProject()
    auth, http = await legacy_auth(tmp_path, jwks, project)
    assert await auth.verify_supabase_token(legacy_token(exp_in=-3600)) is None
    assert await auth.verify_supabase_token("not.a.jwt") is None
    nokey, http2 = await legacy_auth(tmp_path, jwks, project, publishable_key=None)
    assert await nokey.verify_supabase_token(legacy_token()) is None
    assert project.requests == []
    await http.aclose()
    await http2.aclose()


async def test_a_forged_token_on_a_legacy_project_is_sent_to_the_project_which_refuses_it(tmp_path, jwks) -> None:
    project = LegacyProject(401, {"msg": "invalid JWT"})
    auth, http = await legacy_auth(tmp_path, jwks, project)
    assert await auth.verify_supabase_token(hand_made("none", None)) is None
    assert await auth.verify_supabase_token(hand_made("HS256", b"a guess")) is None
    assert len(project.requests) == 2  # the user comes from the project's answer only, so a forged claim gets nowhere
    await http.aclose()


async def test_published_keys_mean_the_project_is_never_asked(tmp_path, jwks) -> None:
    project = LegacyProject()
    http = httpx.AsyncClient(transport=httpx.MockTransport(project))
    auth = AuthService(settings_for(tmp_path), http=http)
    assert await auth.verify_supabase_token(legacy_token()) is None  # HS256 is not an algorithm of any published key
    assert project.requests == []
    await http.aclose()


# ---------- through the app ----------
def test_a_signed_in_user_reaches_the_api_and_health_lists_accounts(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs) as client:
        health = client.get("/health").json()
        assert "accounts" in health["features"] and "agent" in health["features"] and health["authRequired"] is True
        assert client.get("/threads").status_code == 401 and client.get("/runs/mine").status_code == 401
        token = make_token()
        assert client.get("/threads", headers=bearer(token)).json() == {"threads": []}
        assert client.get("/runs/mine", headers=bearer(token)).json() == {"runs": []}


def test_a_token_that_is_not_valid_is_a_401_everywhere(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs) as client:
        for name, make in REFUSED.items():
            for path in ("/threads", "/runs/mine"):
                res = client.get(path, headers=bearer(make()))
                assert res.status_code == 401, (name, path)


def test_the_access_code_still_signs_in_next_to_accounts(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs) as client:
        token = client.post("/auth/session", json={"accessCode": "letmein"}).json()["token"]
        assert client.get("/runs/mine", headers=bearer(token)).status_code == 200
        assert client.post("/auth/session", json={"accessCode": "wrong"}).status_code == 401


def test_a_run_and_a_chat_follow_the_user_to_another_device_and_not_to_another_user(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs) as client:
        laptop, phone, other = bearer(make_token()), bearer(make_token()), bearer(make_token(sub=USER_B, email="b@example.com"))
        assert laptop != phone  # two fresh tokens of the same user
        started = client.post("/runs", json={**RUN, "threadId": THREAD}, headers=laptop)
        assert started.status_code == 201
        run_id = started.json()["runId"]
        assert client.app.state.api.auth.read_stream_token(started.json()["streamToken"]) == (run_id, f"u:{USER_A}")

        mine = client.get("/runs/mine", headers=phone).json()["runs"]
        assert [(r["id"], r["threadId"]) for r in mine] == [(run_id, THREAD)]
        assert client.get("/runs/mine", headers=other).json() == {"runs": []}

        client.post(f"/threads/{THREAD}/messages", json={"text": "what can you build?"}, headers=laptop)
        assert [t["role"] for t in client.get(f"/threads/{THREAD}", headers=phone).json()["turns"]] == ["user", "verity"]
        assert client.get(f"/threads/{THREAD}", headers=other).status_code == 404
        assert [t["id"] for t in client.get("/threads", headers=phone).json()["threads"]] == [THREAD]
        assert client.get("/threads", headers=other).json() == {"threads": []}

        assert client.post(f"/runs/{run_id}/stop", headers=other).status_code == 403
        assert client.post(f"/runs/{run_id}/stop", headers=phone).status_code == 202


def test_another_device_gets_a_stream_token_for_the_users_running_run_and_nobody_else_does(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs) as client:
        laptop, phone, other = bearer(make_token()), bearer(make_token()), bearer(make_token(sub=USER_B, email="b@example.com"))
        run_id = client.post("/runs", json=RUN, headers=laptop).json()["runId"]
        res = client.post(f"/runs/{run_id}/stream-token", headers=phone)  # the phone never saw the 201 of POST /runs
        assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
        assert client.app.state.api.auth.read_stream_token(res.json()["streamToken"]) == (run_id, f"u:{USER_A}")
        assert client.post(f"/runs/{run_id}/stream-token", headers=other).status_code == 403
        assert client.post(f"/runs/{run_id}/stream-token").status_code == 401
        assert client.post("/runs/r_nope/stream-token", headers=phone).status_code == 404
        client.post(f"/runs/{run_id}/stop", headers=phone)


def test_with_auth_off_a_valid_token_is_still_the_user_and_a_bad_one_is_anonymous(tmp_path, packs, jwks) -> None:
    with app_client(tmp_path, packs, required=False) as client:
        token = bearer(make_token())
        started = client.post("/runs", json=RUN, headers=token)
        assert started.status_code == 201
        assert [r["id"] for r in client.get("/runs/mine", headers=token).json()["runs"]] == [started.json()["runId"]]
        assert client.get("/runs/mine").json() == {"runs": []}  # no token: the anonymous session of this IP
        assert client.get("/runs/mine", headers=bearer(make_token(OTHER_KEY))).json() == {"runs": []}
        client.post(f"/runs/{started.json()['runId']}/stop", headers=token)


def test_health_has_no_accounts_when_sign_in_is_not_configured(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    manager = RunManager(settings, store, FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings)), FakeToolRunner(), packs, threads=store)
    with TestClient(create_app(settings, manager, startup=[store.open], shutdown=[store.close])) as client:
        assert "accounts" not in client.get("/health").json()["features"]


async def _sandbox_ok(self) -> None:
    return None


def test_build_app_uses_the_supabase_store_when_it_is_configured(tmp_path, monkeypatch: pytest.MonkeyPatch, jwks) -> None:
    monkeypatch.setattr(LocalDockerRunner, "_check_sandbox", _sandbox_ok)
    local = settings_for(tmp_path, secret_key=SecretStr("sb_secret_" + "t" * 30))
    local = local.model_copy(update={"threads": local.threads.model_copy(update={"store": "auto"})})
    app = main_module.build_app(local)
    assert isinstance(app.state.api.service._threads, SupabaseThreadStore)
    plain = main_module.build_app(make_settings(tmp_path / "data2"))
    assert isinstance(plain.state.api.service._threads, SqliteStore)


# ---------- settings and the production guard ----------
def test_the_supabase_env_names_map_onto_the_supabase_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_abc")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", PUBLISHABLE)
    settings = load_settings(provider="fake")
    assert settings.supabase.url == URL and settings.supabase.secret_key.get_secret_value() == "sb_secret_abc"
    assert settings.supabase.publishable_key.get_secret_value() == PUBLISHABLE
    assert settings.supabase.jwks_cache_s == 600 and settings.supabase.timeout_s == 8 and settings.supabase.host_suffix == ".supabase.co"
    assert settings.supabase_auth_enabled() and settings.threads_store() == "supabase"
    assert "sb_secret_abc" not in repr(settings.supabase)  # a SecretStr never prints
    monkeypatch.setenv("VERITY_THREADS__STORE", "sqlite")
    assert load_settings(provider="fake").threads_store() == "sqlite"  # "sqlite" forces local


def production(**update: Any):  # noqa: ANN003, ANN201
    base = load_settings(provider="fake")
    return base.model_copy(
        update={
            "env": "production",
            "provider": "nebius",
            "llm": base.llm.model_copy(update={"base_url": "https://api.tokenfactory.nebius.com/v1", "api_key": SecretStr("k")}),
            "auth": AuthConfig(required=True),
            "signing_secret": SecretStr("s" * 40),
            "access_code_hashes": SecretStr(f"team:{code_hash('letmein')}"),
            "cors_origins": "https://verity.example",
            "research": base.research.model_copy(update={"adapter": "off"}),
            "supabase": SupabaseConfig(),
            "threads": base.threads.model_copy(update={"store": "auto"}),
            **update,
        }
    )


def problems(**update: Any) -> str:  # noqa: ANN401
    return " | ".join(production(**update).production_problems())


def test_production_guard_for_supabase() -> None:
    assert production().production_problems() == []  # access codes alone are enough
    good = SupabaseConfig(url=URL, secret_key=SecretStr("sb_secret_x"))
    assert production(supabase=good).production_problems() == []
    for bad in ("http://abcdefghijkl.supabase.co", "https://abcdefghijkl.example.com", "https://evilsupabase.co", "https://supabase.co.evil.com", "https://supabase.co"):
        assert "SUPABASE_URL" in problems(supabase=SupabaseConfig(url=bad, secret_key=SecretStr("sb_secret_x"))), bad
    assert "SUPABASE_SECRET_KEY" in problems(supabase=SupabaseConfig(url=URL), threads=production().threads.model_copy(update={"store": "supabase"}))
    assert "SUPABASE_SECRET_KEY" in problems(threads=production().threads.model_copy(update={"store": "supabase"}))
    assert problems(supabase=SupabaseConfig(url=URL)) == ""  # sign-in only: chats stay in SQLite, which is allowed


def test_production_needs_supabase_sign_in_or_an_access_code_and_either_is_enough() -> None:
    assert "SUPABASE_URL or VERITY_ACCESS_CODE_HASHES" in problems(access_code_hashes=None)
    assert problems(access_code_hashes=None, supabase=SupabaseConfig(url=URL)) == ""  # accounts only
    assert problems(supabase=SupabaseConfig(url=URL)) == ""  # both


# ---------- keys in logs ----------
def test_the_supabase_keys_are_redacted_in_a_header_a_repr_and_anywhere_else() -> None:
    secret = "sb_secret_AbC123-xyz_9"
    assert redact(f"apikey: {secret}") == "apikey: [redacted]"
    assert secret not in redact(f"headers={{'apikey': '{secret}', 'Accept': 'application/json'}}")
    assert "'Accept': 'application/json'" in redact(f"headers={{'apikey': '{secret}', 'Accept': 'application/json'}}")
    assert redact(f"GET /rest/v1/threads?apikey={secret}&select=id") == "GET /rest/v1/threads?apikey=[redacted]&select=id"
    assert redact(f"it failed with {secret} in the message") == "it failed with sb_secret_[redacted] in the message"
    assert redact("Apikey = abc") == "Apikey = [redacted]"
