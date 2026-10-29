"""POST /threads/{threadId}/messages, GET /threads and GET /threads/{threadId} through the real app: the real RunManager,
the fake provider and the fake search adapter (no network, $0), the fake tool runner (no Docker)."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from test_sse_and_routes import code_hash
from verity.api.app import create_app
from verity.api.auth import anonymous_sid
from verity.config import AuthConfig
from verity.contracts import CreateRunRequest
from verity.core.parse import live_families
from verity.core.records import ThreadTurnIn
from verity.llm.fake import FakeProvider
from verity.llm.openai_compat import OpenAICompatProvider, _ModelDown
from verity.research import FakeSearchAdapter, ResearchService
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FIXTURE_RUN_ID as RUN_ID
from verity_test_support import FakeToolRunner, make_settings, seed_fixture_run, tmp_cassettes

T1 = "11111111-2222-4333-8444-555555555555"
T2 = "22222222-2222-4333-8444-555555555555"
ME = anonymous_sid("testclient")  # the session of a TestClient when auth is off


class CountingFake(FakeProvider):
    """The fake provider that remembers the purpose of every call it answered."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.purposes: list[str] = []

    async def complete(self, req):  # noqa: ANN001, ANN201
        self.purposes.append(req.tags.get("purpose", req.role))
        return await super().complete(req)


class GlmDown(OpenAICompatProvider):
    """The real provider with its real fallback chain, over a Token Factory whose GLM models time out. One attempt at a
    model (`_complete_one`) is the stub: GLM times out, every other model is answered by the fake provider."""

    def __init__(self, settings) -> None:  # noqa: ANN001
        super().__init__(settings)
        self.fake = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
        self.attempts: list[tuple[str, str]] = []  # (model asked, purpose)

    async def _complete_one(self, req):  # noqa: ANN001, ANN201
        self.attempts.append((req.model, req.tags.get("purpose", req.role)))
        if req.model.startswith("zai-org/"):
            raise _ModelDown("model call timed out", retryable=True)
        return await self.fake.complete(req)


def client_for(tmp_path, packs, *, settings=None, run_of: str | None = None, threads: int = 0, provider=None):  # noqa: ANN001, ANN201
    """A TestClient on the real app. `run_of`: seed the fixture run for that session. `threads`: seed that many threads
    (with one message each) for the anonymous test session. `provider`: a provider other than the counting fake."""
    settings = settings or make_settings(tmp_path / "data")
    provider = provider or CountingFake(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))

    async def prepare() -> None:
        store = SqliteStore(settings)
        await store.open()
        if run_of is not None:
            await seed_fixture_run(store, session_id=run_of)
        for n in range(threads):
            tid = f"{n:08d}-2222-4333-8444-555555555555"
            await store.get_or_create(ME, tid)
            await store.append_turns(ME, tid, [ThreadTurnIn(role="user", text=f"thread number {n}"), ThreadTurnIn(role="verity", text="ok")])
            await asyncio.sleep(0.001)
        await store.close()

    asyncio.run(prepare())
    store = SqliteStore(settings)
    research = ResearchService(settings, store, provider, FakeSearchAdapter(), live_families(packs))
    manager = RunManager(settings, store, provider, FakeToolRunner(delay_s=0.2), packs, research=research, threads=store)
    client = TestClient(create_app(settings, manager, startup=[store.open], shutdown=[store.close]))
    client.provider = provider  # type: ignore[attr-defined]
    return client


def post(client: TestClient, text: str, thread: str = T1, **extra):  # noqa: ANN003, ANN201
    return client.post(f"/threads/{thread}/messages", json={"text": text, **extra})


# ---------- the wire contract ----------
@pytest.mark.parametrize("bad", ["not-a-uuid", "11111111-2222-4333-8444-55555555555", "g1111111-2222-4333-8444-555555555555", "1111111122224333844455555555555"])
def test_a_thread_id_that_is_not_a_uuid_is_422(tmp_path, packs, bad) -> None:
    with client_for(tmp_path, packs) as client:
        res = client.post(f"/threads/{bad}/messages", json={"text": "hello"})
        assert res.status_code == 422 and res.json() == {"error": "The thread id must be a UUID."}
        assert client.get(f"/threads/{bad}").status_code == 422


def test_the_text_is_1_to_300_characters_after_trim(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        for body in ({"text": ""}, {"text": "   "}, {"text": "x" * 301}, {}, {"text": 5}):
            assert client.post(f"/threads/{T1}/messages", json=body).status_code == 422, body
        assert client.post(f"/threads/{T1}/messages", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422
        assert client.post(f"/threads/{T1}/messages", json={"text": "what can you build?" + " " * 400}).status_code == 200
        assert client.post(f"/threads/{T1}/messages", json={"text": "what is a counter?" + "?" * 282}).status_code == 200  # 300 characters


def test_an_uppercase_uuid_is_accepted_and_stored_lowercase(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        res = client.post(f"/threads/{T1.upper()}/messages", json={"text": "what can you build?"})
        assert res.status_code == 200 and res.json()["threadId"] == T1
        assert [t["id"] for t in client.get("/threads").json()["threads"]] == [T1]


def test_health_lists_agent(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        features = client.get("/health").json()["features"]
        assert "agent" in features and "ask" in features and "research" in features


# ---------- the flow ----------
def test_the_first_chip_message_runs_the_check_and_later_ones_do_not(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        first = post(client, "a 16-bit adder")
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        body = first.json()
        assert set(body) == {"threadId", "check", "reply"} and body["threadId"] == T1
        check = body["check"]
        assert check["result"] == "in_range" and check["family"] == "add8" and check["params"] == {"width": 16}
        assert check["verdict"] == "buildable_now" and check["intentClass"] == "in_family" and check["research"]["status"] == "ok"
        assert body["reply"]["kind"] == "answer" and body["reply"]["toolSteps"] == [] and body["reply"]["text"]
        assert client.provider.purposes == ["parse", "research", "agent"]  # the check, its research, the note

        second = post(client, "make it faster").json()
        assert second["check"] is None  # no second fact check
        assert second["reply"]["kind"] == "proposal" and second["reply"]["proposal"]["presetId"] == "add8"
        assert second["reply"]["proposal"]["priority"] == "speed" and second["reply"]["proposal"]["params"] == {"width": 16}
        assert second["reply"]["toolSteps"] == [{"tool": "propose_run", "label": "Made a chip card", "ok": True}]
        assert client.provider.purposes[3:] == ["agent", "agent"]  # the tool step, then the answer: still no parse
        assert client.get("/runs").json() == {"runs": []}  # the proposal started nothing


def test_a_message_whose_parser_model_times_out_still_gets_its_check_from_the_fallback(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    provider = GlmDown(settings)
    with client_for(tmp_path, packs, settings=settings, provider=provider) as client:
        res = post(client, "a 16-bit adder")
        assert res.status_code == 200
        body = res.json()
        assert body["check"]["result"] == "in_range" and body["check"]["family"] == "add8" and body["check"]["params"] == {"width": 16}
        assert body["check"]["verdict"] == "buildable_now" and body["reply"]["kind"] == "answer"
        assert "Worded by DeepSeek V4 Pro;" in body["reply"]["basis"]  # the model that served the note, not GLM 5.3
        glm, deepseek = "zai-org/GLM-5.3", "deepseek-ai/DeepSeek-V4-Pro"
        # GLM was tried once (the parse); it was then skipped for the cooldown, so research and the note went to DeepSeek
        assert provider.attempts == [(glm, "parse"), (deepseek, "parse"), (deepseek, "research"), (deepseek, "agent")]
        assert post(client, "make it faster").status_code == 200
        assert all(model == deepseek for model, _ in provider.attempts[4:])


def test_a_question_gets_no_check_and_the_thread_stays_unchecked(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        asked = post(client, "what can you build?").json()
        assert asked["check"] is None and asked["reply"]["kind"] == "answer"
        assert asked["reply"]["toolSteps"] == [{"tool": "list_blocks", "label": "Read the build list", "ok": True}]
        assert len(asked["reply"]["chips"]) == 4 and "custom block can often be drafted" in asked["reply"]["text"]
        assert client.provider.purposes == ["parse", "agent", "agent"]  # asked, then the agent: no research for a question
        chip = post(client, "a 16-bit adder").json()  # the first chip request of this thread: now it is fact-checked
        assert chip["check"]["verdict"] == "buildable_now" and client.provider.purposes.count("parse") == 2


def test_not_a_chip_gets_an_agent_answer_and_the_next_message_checks_again(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        res = post(client, "I want ice cream")
        body = res.json()
        assert res.status_code == 200 and body["check"] is None and body["reply"] is not None  # no dead-end card
        assert client.provider.purposes[0] == "parse" and "research" not in client.provider.purposes
        again = post(client, "a 16-bit adder").json()
        assert again["check"]["verdict"] == "buildable_now" and again["reply"] is not None
        assert client.provider.purposes.count("parse") == 2


def test_the_seventh_message_in_a_minute_gets_429(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        codes = [post(client, "what can you build?").status_code for _ in range(6)]
        assert codes == [200] * 6
        limited = post(client, "what can you build?")
        assert limited.status_code == 429 and limited.json()["reason"] == "rate_limited" and int(limited.headers["retry-after"]) >= 1


def test_a_refused_message_gives_its_place_in_the_minute_back(tmp_path, packs) -> None:
    base = make_settings(tmp_path / "data")
    settings = base.model_copy(update={"limits": base.limits.model_copy(update={"session_usd": 0.0})})
    with client_for(tmp_path, packs, settings=settings) as client:
        results = [post(client, "a 16-bit adder") for _ in range(8)]  # more than the 6 a minute
        assert {r.status_code for r in results} == {429}
        assert {r.json()["reason"] for r in results} == {"session_budget_exhausted"}  # never rate_limited: each was given back
        assert client.provider.purposes == [] and client.get("/threads").json() == {"threads": []}  # nothing was spent or stored
    daily = base.model_copy(update={"limits": base.limits.model_copy(update={"daily_usd": 0.0})})
    with client_for(tmp_path / "d", packs, settings=daily) as client:
        assert post(client, "hello").json()["reason"] == "budget_exhausted"
    offline = base.model_copy(update={"provider": "openai_compat", "llm": base.llm.model_copy(update={"api_key": None})})  # no key
    with client_for(tmp_path / "m", packs, settings=offline) as client:
        res = post(client, "hello")
        assert res.status_code == 503 and res.json()["reason"] == "maintenance"


def test_a_run_id_must_be_this_sessions(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, run_of="someone-else") as client:
        assert post(client, "how did it go?", runId=RUN_ID).status_code == 403
        assert post(client, "how did it go?", runId="r_nope").status_code == 404
        assert post(client, "how did it go?", runId="no way!").status_code == 404
        assert client.provider.purposes == [] and client.get("/threads").json() == {"threads": []}
        # a refused run id used no place in the minute either
        assert [post(client, "what can you build?").status_code for _ in range(6)] == [200] * 6


def test_a_message_about_a_run_that_is_still_going_is_409_and_costs_nothing(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        started = client.post("/runs", json={"presetId": "add8", "priority": "speed", "mode": "fast", "params": {"width": 16}})
        assert started.status_code == 201
        run_id = started.json()["runId"]
        res = post(client, "how did it go?", runId=run_id)
        assert res.status_code == 409 and res.json() == {"error": "The run is still going. Ask again when it ends."}
        assert {"agent", "parse", "memory"}.isdisjoint(client.provider.purposes) and client.get("/threads").json() == {"threads": []}
        # a refused message used no place in the minute either
        assert [post(client, "what can you build?").status_code for _ in range(6)] == [200] * 6
        client.post(f"/runs/{run_id}/stop")


def test_the_first_message_note_fits_the_verdict_when_the_agent_is_the_fake_one(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        built = post(client, "a 16-bit adder").json()
        assert built["check"]["verdict"] == "buildable_now"
        assert built["reply"]["text"] == "Check the card above and press Run when you are ready, or tell me what to change."
        big = post(client, "I need a GPU", thread=T2).json()
        assert big["check"]["verdict"] == "not_buildable_here" and big["reply"]["kind"] == "answer"
        assert big["reply"]["text"] == "I cannot build that as one block. Ask me what it is made of, or pick the closest chip above."


@pytest.mark.parametrize(
    ("message", "family"),
    [
        ("8-bit adder with carry", "add8"),
        ("make it an 8-bit counter", "cnt8"),
        ("how about a sequence detector", "fsm_seq"),
        ("a pattern detector please", "fsm_seq"),
        ("design a multiply-accumulate array", "mac4"),
        ("build me a MAC", "mac4"),
    ],
)
def test_the_fake_agent_proposes_the_family_the_message_names_not_the_threads_own(tmp_path, packs, message, family) -> None:
    with client_for(tmp_path, packs) as client:
        first = post(client, "a GPU with thousands of cores").json()  # the closest chip is a MAC array: the thread's own family
        assert first["check"]["verdict"] == "not_buildable_here"
        reply = post(client, message).json()["reply"]
        assert reply["kind"] == "proposal" and reply["proposal"]["presetId"] == family
        if "8-bit" in message:
            assert reply["proposal"]["params"]["width"] == 8


def test_a_thread_started_with_a_run_is_already_checked_and_knows_the_run(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, run_of=ME) as client:
        body = post(client, "how did it go?", runId=RUN_ID, focusVersion="v2").json()
        assert body["check"] is None and body["reply"]["kind"] == "answer"
        assert "parse" not in client.provider.purposes  # a run means a fact check already happened
        view = client.get(f"/threads/{T1}").json()
        assert view["runIds"] == [RUN_ID]
        timing = post(client, "what is the timing?").json()["reply"]  # the fake agent calls run_facts for a run's timing
        assert timing["toolSteps"][0]["tool"] == "run_facts" and timing["toolSteps"][0]["label"].startswith("Looked up the timing of")


def test_a_walkthrough_open_tab_and_steps_reach_the_wire(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, run_of=ME) as client:
        walk = post(client, "walk me through it", runId=RUN_ID).json()["reply"]
        assert walk["kind"] == "answer" and len(walk["steps"]) == 9 and walk["bullets"] == []
        assert walk["toolSteps"][0] == {"tool": "walkthrough", "label": "Wrote the walkthrough steps", "ok": True}
        tab = post(client, "please open the sign-off").json()["reply"]
        assert tab["openTab"] == {"runId": RUN_ID, "tab": "signoff"} and tab["toolSteps"][0]["label"] == "Opened the Sign-off tab"


# ---------- reading threads ----------
def logins(client: TestClient, count: int = 2) -> list[dict[str, str]]:
    out = []
    for _ in range(count):
        token = client.post("/auth/session", json={"accessCode": "letmein"}).json()["token"]
        out.append({"Authorization": f"Bearer {token}"})
    return out


def auth_settings(tmp_path):  # noqa: ANN201
    return make_settings(tmp_path / "data").model_copy(
        update={"auth": AuthConfig(required=True), "signing_secret": SecretStr("k" * 40), "access_code_hashes": SecretStr(f"team:{code_hash('letmein')}")}
    )


def test_threads_are_owner_only(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, settings=auth_settings(tmp_path)) as client:
        a, b = logins(client)
        assert client.post(f"/threads/{T1}/messages", json={"text": "hello"}).status_code == 401
        assert client.get("/threads").status_code == 401 and client.get(f"/threads/{T1}").status_code == 401
        sent = client.post(f"/threads/{T1}/messages", json={"text": "what can you build?"}, headers=a)
        assert sent.status_code == 200
        client.post(f"/threads/{T2}/messages", json={"text": "a 16-bit adder"}, headers=a)

        listed = client.get("/threads", headers=a).json()["threads"]
        assert [t["id"] for t in listed] == [T2, T1]  # newest first
        assert set(listed[0]) == {"id", "title", "updatedAt", "runIds"} and listed[0]["title"] == "a 16-bit adder"
        assert listed[0]["updatedAt"].endswith("Z") and listed[0]["runIds"] == []
        assert client.get("/threads", headers=b).json() == {"threads": []}

        view = client.get(f"/threads/{T1}", headers=a)
        assert view.status_code == 200 and view.headers["cache-control"] == "no-store"
        body = view.json()
        assert set(body) == {"threadId", "runIds", "turns"} and body["threadId"] == T1 and body["runIds"] == []
        user, verity = body["turns"]
        assert (user["seq"], user["role"], user["text"]) == (1, "user", "what can you build?") and "toolSteps" not in user
        assert (verity["seq"], verity["role"]) == (2, "verity") and verity["createdAt"].endswith("Z")
        assert verity["toolSteps"] == [{"tool": "list_blocks", "label": "Read the build list", "ok": True}]
        assert "custom block can often be drafted" in verity["text"]

        other = client.get(f"/threads/{T1}", headers=b)
        assert other.status_code == 404 and other.json() == {"error": "Thread not found."}  # never a 403: it does not exist for them
        assert client.get(f"/threads/{'3' * 8}-2222-4333-8444-555555555555", headers=a).status_code == 404
        # b writing to the same id makes b's own thread, not a's
        client.post(f"/threads/{T1}/messages", json={"text": "hello"}, headers=b)
        assert [t["title"] for t in client.get("/threads", headers=b).json()["threads"]] == ["hello"]
        assert len(client.get(f"/threads/{T1}", headers=a).json()["turns"]) == 2


def test_the_list_holds_at_most_50_threads_newest_first(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, threads=53) as client:
        listed = client.get("/threads").json()["threads"]
        assert len(listed) == 50
        assert listed[0]["title"] == "thread number 52" and listed[-1]["title"] == "thread number 3"


def test_threads_survive_a_restart(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        post(client, "a 16-bit adder")
        post(client, "make it faster")
    with client_for(tmp_path, packs) as again:  # a new process on the same data directory
        turns = again.get(f"/threads/{T1}").json()["turns"]
        assert [t["role"] for t in turns] == ["user", "verity", "user", "verity"] and turns[0]["text"] == "a 16-bit adder"
        reply = post(again, "and a bit wider?").json()
        assert reply["check"] is None  # still checked after the restart
        assert reply["reply"]["kind"] == "proposal" and reply["reply"]["proposal"]["params"] == {"width": 16}


# ---------- POST /runs ----------
def test_a_run_started_with_a_thread_id_shows_up_in_that_thread(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        post(client, "a 16-bit adder")
        started = client.post("/runs", json={"presetId": "add8", "priority": "speed", "mode": "fast", "params": {"width": 16}, "threadId": T1.upper()})
        assert started.status_code == 201
        run_id = started.json()["runId"]
        assert client.get(f"/threads/{T1}").json()["runIds"] == [run_id]
        assert [t["runIds"] for t in client.get("/threads").json()["threads"]] == [[run_id]]
        client.post(f"/runs/{run_id}/stop")


def test_a_bad_or_missing_thread_id_never_stops_a_run_from_starting(tmp_path, packs) -> None:
    assert CreateRunRequest(preset_id="add8", priority="area", thread_id="garbage").thread_id is None
    assert CreateRunRequest(preset_id="add8", priority="area", thread_id=T1.upper()).thread_id == T1
    with client_for(tmp_path, packs) as client:
        res = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast", "threadId": "garbage"})
        assert res.status_code == 201
        client.post(f"/runs/{res.json()['runId']}/stop")
        assert client.get("/threads").json() == {"threads": []}  # a thread that does not exist is not made by a run


# ---------- the stored reply, and GET /runs/mine ----------
def test_a_thread_read_returns_the_stored_reply_and_the_check_of_each_verity_turn(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        first = post(client, "a 16-bit adder").json()
        second = post(client, "make it faster").json()
        turns = client.get(f"/threads/{T1}").json()["turns"]
        assert "reply" not in turns[0] and "check" not in turns[0]  # the user's rows carry neither
        assert turns[1]["reply"] == first["reply"] and turns[1]["check"] == first["check"]  # exactly what the POST sent
        assert turns[3]["reply"] == second["reply"] and turns[3]["reply"]["kind"] == "proposal" and "check" not in turns[3]
        assert turns[1]["toolSteps"] == first["reply"]["toolSteps"] and turns[1]["text"]  # text and steps stay as before


def test_a_turn_stored_before_accounts_has_only_its_text_and_steps(tmp_path, packs) -> None:
    async def seed(settings) -> None:  # noqa: ANN001
        store = SqliteStore(settings)
        await store.open()
        await store.get_or_create(ME, T1)
        await store.append_turns(ME, T1, [ThreadTurnIn(role="user", text="hello"), ThreadTurnIn(role="verity", text="hi there")])
        await store.close()

    settings = make_settings(tmp_path / "data")
    asyncio.run(seed(settings))
    with client_for(tmp_path, packs, settings=settings) as client:
        user, verity = client.get(f"/threads/{T1}").json()["turns"]
        assert verity["text"] == "hi there" and verity["toolSteps"] == [] and "reply" not in verity and "check" not in verity


def test_runs_mine_lists_the_runs_this_session_started_and_nobody_elses(tmp_path, packs) -> None:
    with client_for(tmp_path, packs, settings=auth_settings(tmp_path)) as client:
        a, b = logins(client)
        assert client.get("/runs/mine").status_code == 401
        assert client.get("/runs/mine", headers=a).json() == {"runs": []}
        started = client.post("/runs", json={"presetId": "add8", "priority": "speed", "mode": "fast", "params": {"width": 16}, "threadId": T1.upper()}, headers=a)
        assert started.status_code == 201
        run_id = started.json()["runId"]
        mine = client.get("/runs/mine", headers=a)
        assert mine.headers["cache-control"] == "no-store"
        [item] = mine.json()["runs"]
        assert set(item) == {"id", "title", "threadId", "createdAt", "status"}
        assert (item["id"], item["threadId"]) == (run_id, T1) and item["title"] and item["createdAt"].endswith("Z")
        assert item["status"] in ("queued", "running")
        assert client.get("/runs/mine", headers=b).json() == {"runs": []}
        client.post(f"/runs/{run_id}/stop", headers=a)


# ---------- the CPU job on screen (cpuJobId) ----------
JOB_MINE = "cpu_" + "a" * 32
JOB_THEIRS = "cpu_" + "b" * 32


def seed_cpu_jobs(settings, jobs: list[tuple[str, str, str | None]]) -> None:  # noqa: ANN001
    """Save passed CPU jobs, (id, owner, thread id), straight into the store of `settings`."""
    from verity.contracts.cpu import CpuJobView
    from verity.runs.cpu_job import CpuJob

    async def run() -> None:
        store = SqliteStore(settings)
        await store.open()
        for job_id, owner, thread_id in jobs:
            view = CpuJobView(id=job_id, goal="speed", status="passed", created_at="2026-10-28T00:00:00+00:00", blocks=[])
            await store.save_cpu_job(CpuJob(owner=owner, session_id=owner, thread_id=thread_id, view=view))
        await store.close()

    asyncio.run(run())


def watch_facts(client: TestClient) -> list[str]:
    """The <verity_facts> text of every agent turn the client's app prepares from now on."""
    agent = client.app.state.api.service._agent
    seen: list[str] = []
    prepare = agent._prepare

    async def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        prep = await prepare(*args, **kwargs)
        seen.append(prep.facts_text)
        return prep

    agent._prepare = spy
    return seen


def test_the_cpu_job_on_screen_reaches_the_agent_facts_when_it_is_the_owners(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    seed_cpu_jobs(settings, [(JOB_MINE, ME, None)])
    with client_for(tmp_path, packs, settings=settings) as client:
        seen = watch_facts(client)
        assert post(client, "what can you build?", cpuJobId=JOB_MINE).status_code == 200
        assert f"on screen in the right panel: CPU job {JOB_MINE}" in seen[-1]
        assert f"<cpu_job>\non screen in the right panel: CPU job {JOB_MINE}\nCPU job {JOB_MINE}:" in seen[-1]


def test_another_owners_unknown_and_malformed_cpu_job_ids_are_ignored_and_the_message_is_answered(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    seed_cpu_jobs(settings, [(JOB_THEIRS, "someone-else", None)])
    with client_for(tmp_path, packs, settings=settings) as client:
        seen = watch_facts(client)
        for bad in (JOB_THEIRS, "cpu_" + "c" * 32, "cpu_nothex", "../etc/passwd", "CPU_" + "A" * 32):
            reply = post(client, "what can you build?", cpuJobId=bad)
            assert reply.status_code == 200, bad
        assert len(seen) == 5 and not any("on screen in the right panel" in text or JOB_THEIRS in text for text in seen)


def test_a_message_the_bucket_refuses_never_reads_the_cpu_job_on_screen(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    seed_cpu_jobs(settings, [(JOB_MINE, ME, None)])
    with client_for(tmp_path, packs, settings=settings) as client:
        jobs = client.app.state.api.service.cpu_jobs
        get = jobs.get
        reads: list[str] = []

        async def counted(job_id: str, **kw):  # noqa: ANN003, ANN202
            reads.append(job_id)
            return await get(job_id, **kw)

        jobs.get = counted
        assert [post(client, "what can you build?", cpuJobId=JOB_MINE).status_code for _ in range(6)] == [200] * 6
        assert len(reads) == 6
        limited = post(client, "what can you build?", cpuJobId=JOB_MINE)
        assert limited.status_code == 429 and limited.json()["reason"] == "rate_limited"
        assert len(reads) == 6  # the refused message did no job read (a refresh walks every run of the job)


# ---------- who may build a CPU: the route decides from the principal ----------
class SystemsFake(CountingFake):
    """The counting fake that also keeps the system prompt of every agent call."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.systems: list[str] = []

    async def complete(self, req):  # noqa: ANN001, ANN201
        if req.tags.get("purpose") == "agent":
            self.systems.append(req.system)
        return await super().complete(req)


@pytest.mark.parametrize("local_admin,said", [(False, "no (the user is not signed in)"), (True, "yes")])
def test_the_facts_say_whether_this_user_can_build_cpus(tmp_path, packs, local_admin, said) -> None:
    settings = make_settings(tmp_path / "data", auth=AuthConfig(local_admin=local_admin))
    provider = SystemsFake(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    with client_for(tmp_path, packs, settings=settings, provider=provider) as client:
        assert post(client, "what can you build?").status_code == 200
        facts = provider.systems[-1][provider.systems[-1].index('<verity_facts source="code">'):]
        assert f"this user can build CPUs: {said}" in facts
        assert ("Building a CPU needs you to sign in." in facts) is (not local_admin)


def test_the_facts_say_yes_to_a_signed_in_visitor_who_is_not_an_admin(tmp_path, packs) -> None:
    # An access code session of role judge is no admin, but it is signed in, so it can build.
    settings = make_settings(tmp_path / "data", access_code_hashes=SecretStr(f"judge:{code_hash('judgecode')}"))
    provider = SystemsFake(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    with client_for(tmp_path, packs, settings=settings, provider=provider) as client:
        token = client.post("/auth/session", json={"accessCode": "judgecode"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/auth/me", headers=headers).json()["isAdmin"] is False
        reply = client.post(f"/threads/{T1}/messages", json={"text": "what can you build?"}, headers=headers)
        assert reply.status_code == 200
        facts = provider.systems[-1][provider.systems[-1].index('<verity_facts source="code">'):]
        assert "this user can build CPUs: yes" in facts and "needs you to sign in" not in facts
