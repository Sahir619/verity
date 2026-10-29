"""Tests for credits ledger, run credit gating, and admin user creation."""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from test_sse_and_routes import code_hash
from verity.api.app import create_app
from verity.api.auth import anonymous_sid
from verity.config import AuthConfig, LimitsConfig
from verity.contracts import CreateRunRequest
from verity.core.parse import live_families
from verity.llm.fake import FakeProvider
from verity.research import FakeSearchAdapter, ResearchService
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings


def client_for(tmp_path, packs, *, admin_emails=None):
    admin_list = admin_emails or ["admin@verity.com"]
    settings = make_settings(
        tmp_path,
        auth=AuthConfig(
            required=False,
            admin_emails=admin_list,
            initial_credits=3,
        ),
        limits=LimitsConfig(
            concurrent_per_session=5,
            concurrent_global=10,
            runs_per_session_per_hour=10,
        ),
        access_code_hashes=SecretStr(f"team:{code_hash('teamcode')},judge:{code_hash('judgecode')}"),
    )

    store = SqliteStore(settings)
    provider = FakeProvider(settings, delay_s=0.0)
    tools = FakeToolRunner()
    adapter = FakeSearchAdapter()
    research = ResearchService(settings, store, provider, adapter, live_families(packs))
    manager = RunManager(settings, store, provider, tools, packs, research=research, threads=store)
    app = create_app(settings, manager, startup=[store.open], shutdown=[store.close, provider.aclose, research.aclose])
    return TestClient(app)


def test_auth_me_anonymous(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        res = client.get("/auth/me")
        assert res.status_code == 200
        data = res.json()
        assert data["credits"] == 3
        assert data["isAdmin"] is False
        assert data["anonymous"] is True
        assert data["canBuild"] is False  # no session, no CPU builds and runs are charged


def test_auth_me_and_admin_routes_with_team_code(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        # Team role from access code is recognized as admin
        s_res = client.post("/auth/session", json={"accessCode": "teamcode"})
        assert s_res.status_code == 200
        token = s_res.json()["token"]

        headers = {"Authorization": f"Bearer {token}"}
        me_res = client.get("/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["isAdmin"] is True
        assert me_res.json()["role"] == "team"
        assert me_res.json()["canBuild"] is True

        # Admin user creation
        create_res = client.post(
            "/admin/users",
            headers=headers,
            json={"email": "newuser@example.com", "password": "securepassword123", "credits": 5},
        )
        assert create_res.status_code == 201
        created = create_res.json()
        assert created["ok"] is True
        assert created["email"] == "newuser@example.com"
        assert created["credits"] == 5

        # Check credits set for new user
        owner = f"u:{created['userId']}"
        cred_res = client.post(
            "/admin/credits",
            headers=headers,
            json={"owner": owner, "credits": 10},
        )
        assert cred_res.status_code == 200
        assert cred_res.json()["credits"] == 10


def test_admin_routes_forbidden_for_non_admin(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        # Judge role is not admin
        s_res = client.post("/auth/session", json={"accessCode": "judgecode"})
        assert s_res.status_code == 200
        token = s_res.json()["token"]

        headers = {"Authorization": f"Bearer {token}"}
        me_res = client.get("/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["isAdmin"] is False
        assert me_res.json()["canBuild"] is True  # signed in, so it builds, but it is still no admin

        # Attempt to create user fails with 403
        create_res = client.post(
            "/admin/users",
            headers=headers,
            json={"email": "forbidden@example.com", "password": "password123"},
        )
        assert create_res.status_code == 403
        assert "Admin privileges required" in create_res.json()["error"]
        # The other admin routes refuse it too: being able to build opens no admin page.
        assert client.get("/admin/users", headers=headers).status_code == 403
        assert client.post("/admin/credits", headers=headers, json={"owner": "x", "credits": 99}).status_code == 403


def test_runs_gate_by_credits(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        # Initial credits = 3
        me_res = client.get("/auth/me")
        assert me_res.json()["credits"] == 3

        # Run 1
        r1 = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast"})
        assert r1.status_code == 201
        assert client.get("/auth/me").json()["credits"] == 2

        # Run 2
        r2 = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast"})
        assert r2.status_code == 201
        assert client.get("/auth/me").json()["credits"] == 1

        # Run 3
        r3 = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast"})
        assert r3.status_code == 201
        assert client.get("/auth/me").json()["credits"] == 0

        # Run 4 is rejected because credits are exhausted
        r4 = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast"})
        assert r4.status_code == 429
        data = r4.json()
        assert data["reason"] == "credits_exhausted"
        assert "You have used all your run credits" in data["error"]


@pytest.mark.parametrize(("env", "admin"), [("development", True), ("test", False), ("production", False)])
def test_local_admin_makes_an_anonymous_visitor_admin_only_in_development(tmp_path, packs, env, admin):
    settings = make_settings(tmp_path, env=env, auth=AuthConfig(required=False, local_admin=True))
    assert ("auth.local_admin must be off in production" in settings.production_problems()) is (env == "production")
    store = SqliteStore(settings)
    provider = FakeProvider(settings, delay_s=0.0)
    manager = RunManager(settings, store, provider, FakeToolRunner(), packs, threads=store)
    app = create_app(settings, manager, startup=[store.open], shutdown=[store.close, provider.aclose])
    with TestClient(app) as client:
        data = client.get("/auth/me").json()
        assert data["anonymous"] is True and data["isAdmin"] is admin and data["isSuperAdmin"] is admin
        assert data["canBuild"] is admin  # the local-admin visitor counts as signed in, a plain anonymous one does not


def test_auth_me_anonymous_is_not_admin_without_local_admin(tmp_path, packs):
    settings = make_settings(tmp_path, env="development", auth=AuthConfig(required=False))
    store = SqliteStore(settings)
    provider = FakeProvider(settings, delay_s=0.0)
    manager = RunManager(settings, store, provider, FakeToolRunner(), packs, threads=store)
    app = create_app(settings, manager, startup=[store.open], shutdown=[store.close, provider.aclose])
    with TestClient(app) as client:
        assert client.get("/auth/me").json()["isAdmin"] is False


def _run_body():
    return {"presetId": "add8", "priority": "area", "mode": "fast"}


def _judge_with_no_credits(client):
    """A signed-in visitor that is no admin (an access code session of role judge), its credits set to 0 by a team session."""
    judge = client.post("/auth/session", json={"accessCode": "judgecode"}).json()["token"]
    team = client.post("/auth/session", json={"accessCode": "teamcode"}).json()["token"]
    judge_headers, team_headers = {"Authorization": f"Bearer {judge}"}, {"Authorization": f"Bearer {team}"}
    sid = client.app.state.api.auth.read_session(judge).sid
    res = client.post("/admin/credits", headers=team_headers, json={"owner": sid, "credits": 0})
    assert res.status_code == 200 and client.get("/auth/me", headers=judge_headers).json()["credits"] == 0
    return judge_headers, sid


def test_a_signed_in_non_admin_with_no_credits_starts_chip_runs_and_is_never_charged(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        headers, sid = _judge_with_no_credits(client)
        service = client.app.state.api.service
        ids = []
        for _ in range(3):
            res = client.post("/runs", headers=headers, json=_run_body())
            assert res.status_code == 201, res.text
            ids.append(res.json()["runId"])
            assert client.get("/auth/me", headers=headers).json()["credits"] == 0  # not charged, not driven negative
        # The runs are not paused at a credit checkpoint: each ends as a finished run.
        for run_id in ids:
            for _ in range(200):
                record = client.portal.call(service.get_run, run_id)
                if record.status not in ("queued", "running"):
                    break
                time.sleep(0.1)
            assert record.status == "done", record.status
        assert client.get("/auth/me", headers=headers).json()["credits"] == 0


def test_an_anonymous_visitor_is_charged_for_chip_runs_and_cannot_build_a_cpu(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        assert client.post("/runs", json=_run_body()).status_code == 201
        assert client.get("/auth/me").json()["credits"] == 2  # still charged
        refused = client.post("/cpu-jobs", json={"specId": "rv32i_micro", "goal": "area"})
        assert refused.status_code == 403 and refused.json()["error"] == "Sign in to build a CPU."
        assert client.get("/auth/me").json()["credits"] == 2


def test_the_daily_budget_still_refuses_a_signed_in_visitor(tmp_path, packs):
    client = client_for(tmp_path, packs)
    with client:
        headers, _ = _judge_with_no_credits(client)
        service = client.app.state.api.service
        settings = client.app.state.api.settings
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        client.portal.call(service._store.add_daily_spend, day, settings.limits.daily_usd)
        res = client.post("/runs", headers=headers, json=_run_body())
        assert res.status_code == 429 and res.json()["reason"] == "budget_exhausted"


def test_a_run_out_of_credits_mid_run_stops_at_a_checkpoint(tmp_path, packs):
    """An anonymous visitor's last credit: the credit check after the baseline finds none left, so the run ends as
    stopped (credits_exhausted) with a checkpoint that POST /runs/{id}/resume can continue, instead of crashing."""
    client = client_for(tmp_path, packs)
    with client:
        service = client.app.state.api.service
        client.portal.call(service._store.set_credits, anonymous_sid("testclient"), 1)
        res = client.post("/runs", json=_run_body())
        assert res.status_code == 201, res.text
        run_id = res.json()["runId"]
        for _ in range(200):
            record = client.portal.call(service.get_run, run_id)
            if record.status not in ("queued", "running"):
                break
            time.sleep(0.1)
        assert (record.status, record.error_reason) == ("stopped", "credits_exhausted")
        assert client.portal.call(service._store.read_run_file, run_id, "checkpoint.json") is not None
        # The stored status turns stopped a moment before the worker leaves the live set; resume only after it has.
        for _ in range(100):
            if run_id not in service._live:
                break
            time.sleep(0.05)
        assert run_id not in service._live
        # With credits refilled, the owner resumes it from the checkpoint and it finishes.
        client.portal.call(service._store.set_credits, anonymous_sid("testclient"), 2)
        resumed = client.post(f"/runs/{run_id}/resume")
        assert resumed.status_code < 300, resumed.text
        for _ in range(200):
            record = client.portal.call(service.get_run, run_id)
            if record.status not in ("queued", "running", "stopped"):
                break
            time.sleep(0.1)
        assert record.status == "done", (record.status, record.error_reason)
