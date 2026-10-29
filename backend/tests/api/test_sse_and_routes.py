"""SSE framing and the HTTP routes, against a stub RunService (no orchestrator, no store)."""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from verity.api import sse
from verity.api.app import create_app
from verity.config import AuthConfig, Settings, load_settings
from verity.contracts import CaptionEvent, EventBase, HealthResponse, RunPhaseEvent, parse_event_json
from verity.core import captions as cap
from verity.core.budget import BudgetLimits
from verity.core.errors import AdmissionDenied, Conflict, NotFound
from verity.core.family import render_pack
from verity.core.pack import TaskPack
from verity.core.records import RunRecord


def stamp(event: EventBase, seq: int) -> EventBase:
    return event.model_copy(update={"seq": seq, "t_ms": seq * 10})


def run_events() -> list[EventBase]:
    """The ending of every run: ... run done, then the `Done` caption AFTER it (as in golden.json)."""
    return [
        stamp(RunPhaseEvent(phase="running"), 1),
        stamp(CaptionEvent(text="Spec read.", chapter="Spec"), 2),
        stamp(RunPhaseEvent(phase="done"), 3),
        stamp(CaptionEvent(text="Done. 1 versions.", chapter="Done"), 4),
    ]


async def aiter_of(events: list[EventBase], *, then_sleep: float = 0.0) -> AsyncIterator[EventBase]:
    for event in events:
        yield event
    if then_sleep:
        await asyncio.sleep(then_sleep)


async def collect(agen: AsyncIterator[EventBase], ping_s: float = 5.0, first: EventBase | None = None) -> list[bytes]:
    return [frame async for frame in sse.sse_body(agen, ping_s, first)]


# ---------- framing ----------
async def test_frames_start_with_retry_and_carry_ids() -> None:
    frames = await collect(aiter_of(run_events()))
    assert frames[0] == b"retry: 3000\n\n"
    ids = [f.split(b"\n")[0] for f in frames[1:]]
    assert ids == [b"id: 1", b"id: 2", b"id: 3", b"id: 4"]
    parsed = parse_event_json(frames[-1].split(b"data: ", 1)[1].strip())
    assert isinstance(parsed, CaptionEvent) and parsed.seq == 4


async def test_stream_continues_past_the_terminal_run_event_until_the_service_ends_it() -> None:
    """Regression: the final `Done` caption comes after `run done` and must reach the browser."""
    frames = await collect(aiter_of(run_events()))
    assert b"Done. 1 versions." in frames[-1]


async def test_first_peeked_event_is_not_lost() -> None:
    events = run_events()
    frames = await collect(aiter_of(events[1:]), first=events[0])
    assert [f.split(b"\n")[0] for f in frames[1:]] == [b"id: 1", b"id: 2", b"id: 3", b"id: 4"]


async def test_ping_comment_while_idle() -> None:
    async def slow() -> AsyncIterator[EventBase]:
        await asyncio.sleep(0.25)
        yield stamp(RunPhaseEvent(phase="running"), 1)

    frames = await collect(slow(), ping_s=0.05)
    assert sse.PING_FRAME in frames
    assert frames[-1].startswith(b"id: 1")


async def test_a_stuck_stream_after_the_terminal_event_is_cut_after_the_grace_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse, "TERMINAL_GRACE_S", 0.1)
    events = run_events()[:3]
    frames = await asyncio.wait_for(collect(aiter_of(events, then_sleep=30)), timeout=5)
    assert frames[-1].startswith(b"id: 3")
    await asyncio.sleep(0.05)  # let the scheduled cleanup task finish


# ---------- routes ----------
class StubService:
    def __init__(self, settings: Settings, packs: Mapping[str, TaskPack]) -> None:
        self.settings = settings
        self.packs = dict(packs)
        self.runs: dict[str, RunRecord] = {}
        self.events: dict[str, list[EventBase]] = {}
        self.steers: list[object] = []
        self.stops: list[str] = []
        self.deny: str | None = None
        self.rendered: TaskPack | None = None
        self.charged: list[bool] = []  # charge_credits of each create_run call

    async def start(self) -> None: ...
    async def shutdown(self) -> None: ...

    def presets(self) -> Mapping[str, TaskPack]:
        return {k: p for k, p in self.packs.items() if p.live_preset}

    async def create_run(
        self, *, session_id: str, preset_id: str, priority: str, mode: str, params: Mapping[str, object] | None = None,
        charge_credits: bool = True,
    ) -> RunRecord:
        self.charged.append(charge_credits)
        if self.deny:
            raise AdmissionDenied(self.deny)
        self.rendered = render_pack(self.packs[preset_id], params, self.settings.data_dir / "packs")
        run_id = f"r_{len(self.runs) + 1:04d}"
        record = RunRecord(
            id=run_id,
            session_id=session_id,
            preset_id=preset_id,
            priority=priority,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            status="running",
            created_at=datetime.now(UTC),
            budget=BudgetLimits.from_settings(self.settings),
            task_pack_sha256="0" * 64,
            params=dict(params) if params else None,
            title=self.rendered.title if self.rendered else preset_id,
        )
        self.runs[run_id] = record
        self.events[run_id] = run_events()
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    async def stream(self, run_id: str, after_seq: int = 0) -> AsyncIterator[EventBase]:
        if run_id not in self.runs:
            raise NotFound(run_id)
        for event in self.events[run_id]:
            if event.seq > after_seq:
                yield event

    async def steer(self, run_id: str, session_id: str, request: object) -> None:
        if self.runs[run_id].status != "running":
            raise Conflict("the run has ended")
        self.steers.append(request)

    async def stop(self, run_id: str, session_id: str) -> None:
        self.stops.append(run_id)

    async def run_log(self, run_id: str):  # noqa: ANN201
        from verity.runs.runlog import build_run_log

        return build_run_log(self.runs[run_id], self.events[run_id], self.settings)

    async def health(self) -> HealthResponse:
        return HealthResponse(live_enabled=True, cap_usd=0.5, provider="fake", models={"planner": "m"}, features=["params"])

    async def list_runs(self, limit: int = 50, offset: int = 0):  # noqa: ANN201
        from verity.contracts import RunSummaryItem, RunsResponse

        items = [
            RunSummaryItem(
                id=r.id,
                preset_id=r.preset_id,
                params=r.params,
                title=r.title or r.preset_id,
                status=r.status,
                finished_at=None,
                winner=None,
            )
            for r in self.runs.values()
        ]
        return RunsResponse(runs=items[offset : offset + limit])


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def make_client(
    tmp_path, packs, *, required: bool, stream_ttl_s: int = 1_800, secret: SecretStr | None = SecretStr("k" * 40)
) -> tuple[TestClient, StubService]:
    base = load_settings(provider="fake")
    settings = base.model_copy(
        update={
            "env": "development",
            "data_dir": tmp_path / "data",
            "auth": AuthConfig(required=required, stream_ttl_s=stream_ttl_s),
            "signing_secret": secret,
            "access_code_hashes": SecretStr(f"team:{code_hash('letmein')}"),
            "supabase": type(base.supabase)(),  # never the developer's Supabase keys
        }
    )
    service = StubService(settings, packs)
    return TestClient(create_app(settings, service)), service  # type: ignore[arg-type]


def login(client: TestClient) -> dict[str, str]:
    res = client.post("/auth/session", json={"accessCode": "letmein"})
    assert res.status_code == 200, res.text
    return {"Authorization": "Bearer " + res.json()["token"]}


def test_health_is_camel_case_and_reports_auth_required(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=True)
    with client:
        body = client.get("/health").json()
    assert body["liveEnabled"] is True and body["authRequired"] is True and body["capUsd"] == 0.5
    assert "reason" not in body


def test_login_with_a_good_and_a_bad_code(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=True)
    with client:
        good = client.post("/auth/session", json={"accessCode": "letmein"})
        bad = client.post("/auth/session", json={"accessCode": "nope"})
        malformed = client.post("/auth/session", json={"wrong": 1})
    assert good.status_code == 200 and set(good.json()) == {"token", "expiresAt"}
    assert good.json()["expiresAt"].endswith("Z")
    assert bad.status_code == 401 and bad.json() == {"error": "Invalid access code."}
    assert malformed.status_code == 422


def test_auth_required_blocks_runs_without_a_token(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=True)
    with client:
        res = client.post("/runs", json={"presetId": "mac4", "priority": "area"})
        assert res.status_code == 401
        ok = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=login(client))
    assert ok.status_code == 201
    assert set(ok.json()) == {"runId", "capUsd", "streamToken"}


def test_anonymous_runs_when_auth_is_off(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=False)
    with client:
        res = client.post("/runs", json={"presetId": "mac4", "priority": "area"})
        assert res.status_code == 201
        run_id = res.json()["runId"]
        log = client.get(f"/runs/{run_id}/log")
    assert log.status_code == 200
    assert log.json()["format"] == "verity.runlog/1" and log.json()["meta"]["sample"] is False


@pytest.mark.parametrize(
    "body",
    [{"presetId": "nope", "priority": "area"}, {"presetId": "mac4", "priority": "fastest"}, {"presetId": "mac4"}, {}],
)
def test_bad_create_bodies_are_422(tmp_path, packs, body: dict) -> None:
    client, _ = make_client(tmp_path, packs, required=False)
    with client:
        assert client.post("/runs", json=body).status_code == 422


def test_admission_denial_is_429_with_a_reason(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    service.deny = "busy"
    with client:
        res = client.post("/runs", json={"presetId": "mac4", "priority": "area"})
    assert res.status_code == 429 and res.json()["reason"] == "busy"


def test_events_stream_delivers_everything_including_the_final_caption(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=False)
    with client:
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}).json()["runId"]
        res = client.get(f"/runs/{run_id}/events")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache"
    assert res.text.startswith("retry: 3000")
    assert "id: 4" in res.text and "Done. 1 versions." in res.text


def test_events_resume_after_last_event_id(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=False)
    with client:
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}).json()["runId"]
        res = client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    assert "id: 1" not in res.text and "id: 2" not in res.text and "id: 3" in res.text


def test_events_of_a_finished_run_with_nothing_left_answer_204(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    with client:
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}).json()["runId"]
        service.runs[run_id] = service.runs[run_id].model_copy(update={"status": "done"})
        res = client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "4"})
    assert res.status_code == 204


def test_replay_of_a_finished_run_asks_the_browser_to_reconnect_at_once(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    with client:
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}).json()["runId"]
        service.runs[run_id] = service.runs[run_id].model_copy(update={"status": "done"})
        res = client.get(f"/runs/{run_id}/events")
    assert res.status_code == 200 and res.text.startswith("retry: 250")


def test_stream_token_rules_when_auth_is_required(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=True)
    with client:
        headers = login(client)
        created = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=headers).json()
        run_id, token = created["runId"], created["streamToken"]
        assert client.get(f"/runs/{run_id}/events").status_code == 401
        assert client.get(f"/runs/{run_id}/events?st=garbage").status_code == 401
        assert client.get(f"/runs/other/events?st={token}").status_code == 401  # token is for one run
        assert client.get(f"/runs/{run_id}/events?st={token}").status_code == 200


def test_ownership_unknown_run_and_steer_stop(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=True)
    with client:
        owner = login(client)
        stranger = login(client)  # a second login is a different session id
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=owner).json()["runId"]

        assert client.get(f"/runs/{run_id}/log", headers=stranger).status_code == 403
        assert client.get("/runs/r_9999/log", headers=owner).status_code == 404
        assert client.get("/runs/bad id!/log", headers=owner).status_code == 404

        pin = {"kind": "pin", "blockId": "blk-mult", "pin": "Minimize area"}
        assert client.post(f"/runs/{run_id}/steer", json=pin, headers=owner).status_code == 202
        assert client.post(f"/runs/{run_id}/steer", json=pin, headers=stranger).status_code == 403
        assert client.post(f"/runs/{run_id}/steer", json={"kind": "bogus"}, headers=owner).status_code == 422
        assert len(service.steers) == 1

        assert client.post(f"/runs/{run_id}/stop", headers=owner).status_code == 202
        assert service.stops == [run_id]


def test_health_lists_features(tmp_path, packs) -> None:
    client, _ = make_client(tmp_path, packs, required=False)
    with client:
        assert client.get("/health").json()["features"] == ["params"]


def test_run_with_bad_params_is_422_with_a_sentence(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    with client:
        res = client.post("/runs", json={"presetId": "mac4", "priority": "area", "params": {"lanes": 9}})
    assert res.status_code == 422
    assert "1 to 8" in res.json()["error"]
    assert not service.runs


def test_run_with_valid_params_renders_the_pack(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    with client:
        res = client.post(
            "/runs", json={"presetId": "mac4", "priority": "area", "params": {"lanes": 6, "width": 6, "cycles": 20}}
        )
    assert res.status_code == 201, res.text
    assert service.rendered is not None
    a = service.rendered.port("a")
    assert (a.count, a.width) == (6, 6)


def test_finished_run_log_is_publicly_readable(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=True)
    with client:
        owner = login(client)
        stranger = login(client)
        run_id = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=owner).json()["runId"]

        # Active run: stranger is rejected with 403
        assert client.get(f"/runs/{run_id}/log", headers=stranger).status_code == 403

        # Mark run finished (done)
        service.runs[run_id] = service.runs[run_id].model_copy(update={"status": "done"})

        # Finished run: stranger gets 200
        res_stranger = client.get(f"/runs/{run_id}/log", headers=stranger)
        assert res_stranger.status_code == 200
        assert res_stranger.json()["meta"]["runId"] == run_id

        # Finished run: unauthenticated request without token gets 200
        res_anon = client.get(f"/runs/{run_id}/log")
        assert res_anon.status_code == 200
        assert res_anon.json()["meta"]["runId"] == run_id


def test_list_runs_endpoint(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    with client:
        # Empty at start
        res = client.get("/runs")
        assert res.status_code == 200
        assert res.json() == {"runs": []}

        # Create a run with params
        created = client.post(
            "/runs",
            json={"presetId": "mac4", "priority": "area", "params": {"lanes": 6, "width": 6, "cycles": 20}},
        ).json()
        run_id = created["runId"]

        res = client.get("/runs")
        assert res.status_code == 200
        data = res.json()
        assert len(data["runs"]) == 1
        item = data["runs"][0]
        assert item["id"] == run_id
        assert item["presetId"] == "mac4"
        assert item["params"] == {"lanes": 6, "width": 6, "cycles": 20}
        assert item["status"] == "running"
        assert item["winner"] is None


# ---------- reconnect after a restart, expired stream tokens, sandbox down ----------
def test_stream_token_survives_a_backend_restart_without_a_configured_secret(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=True, secret=None)
    with client:
        created = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=login(client)).json()
    run_id, token = created["runId"], created["streamToken"]

    # Restart: a new app (new AuthService) over the same data dir and the same runs. The run is still running.
    restarted = TestClient(create_app(service.settings, service))  # type: ignore[arg-type]
    with restarted:
        res = restarted.get(f"/runs/{run_id}/events?st={token}")
    assert res.status_code == 200 and "id: 4" in res.text


def test_expired_stream_token_replays_a_finished_run_but_never_opens_a_running_one(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=True, stream_ttl_s=-1)
    with client:
        created = client.post("/runs", json={"presetId": "mac4", "priority": "area"}, headers=login(client)).json()
        run_id, token = created["runId"], created["streamToken"]

        assert client.get(f"/runs/{run_id}/events?st={token}").status_code == 401  # running: expired is refused
        assert client.get(f"/runs/{run_id}/events").status_code == 401

        service.runs[run_id] = service.runs[run_id].model_copy(update={"status": "error"})
        expired = client.get(f"/runs/{run_id}/events?st={token}")
        assert expired.status_code == 200 and "Done. 1 versions." in expired.text
        # Same public rule as GET /runs/{id}/log: no token and a junk token both replay a finished run.
        assert client.get(f"/runs/{run_id}/events").status_code == 200
        assert client.get(f"/runs/{run_id}/events?st=garbage").status_code == 200
        assert client.get(f"/runs/{run_id}/events?st={token}", headers={"Last-Event-ID": "4"}).status_code == 204
        assert client.get(f"/runs/r_9999/events?st={token}").status_code == 401  # unknown run: still no stream


def test_sandbox_down_refuses_new_runs_with_503_and_refunds_the_run_budget(tmp_path, packs) -> None:
    client, service = make_client(tmp_path, packs, required=False)
    service.deny = "sandbox_down"
    with client:
        for _ in range(5):  # more than runs_per_session_per_hour: a refusal never uses up the hourly run budget
            res = client.post("/runs", json={"presetId": "mac4", "priority": "area"})
            assert res.status_code == 503
            assert res.json() == {"error": cap.DOCKER_DOWN, "reason": "sandbox_down"}
        service.deny = None
        assert client.post("/runs", json={"presetId": "mac4", "priority": "area"}).status_code == 201
