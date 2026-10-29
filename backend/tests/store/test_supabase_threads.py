"""SupabaseThreadStore against an in-memory stand-in for the Data API (PostgREST): no network, no Supabase project.

The fake answers exactly the calls the store makes (filters eq, gt, in, not.is.null; order; limit; select; upserts that
ignore duplicates; PATCH with a returned representation) and refuses what Postgres would refuse: an unknown column, a
value that is not a uuid, a role outside user and verity, a duplicate key, a turn without its thread. Every request must
carry the secret key in the `apikey` header and nothing in `Authorization`.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from verity.api.app import create_app
from verity.api.auth import anonymous_sid
from verity.config import SupabaseConfig
from verity.core.errors import NotFound, StoreUnavailable
from verity.core.protocols import ThreadStore
from verity.core.records import THREAD_REPLY_MAX_BYTES, THREAD_USER_MAX, THREAD_VERITY_MAX, ThreadTurnIn
from verity.llm.fake import FakeProvider
from verity.research import FakeSearchAdapter, ResearchService
from verity.core.parse import live_families
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity.store.supabase_threads import SupabaseThreadStore
from verity_test_support import FakeToolRunner, make_settings, tmp_cassettes

URL = "https://abcdefghijkl.supabase.co"
SECRET = "sb_secret_" + "t" * 30
TID = "11111111-2222-4333-8444-555555555555"
T2 = "22222222-2222-4333-8444-555555555555"
SCHEMA = Path(__file__).resolve().parents[2] / "supabase" / "schema.sql"

PRIMARY_KEYS = {
    "threads": ("owner", "id"),
    "thread_turns": ("owner", "thread_id", "seq"),
    "user_runs": ("owner", "run_id"),
    "user_credits": ("owner",),
}
COLUMNS = {
    "threads": {"owner", "id", "title", "facts", "summary", "summarized_through", "created_at", "updated_at"},
    "thread_turns": {"owner", "thread_id", "seq", "role", "text", "tool_steps", "reply", "created_at"},
    "user_runs": {"owner", "run_id", "thread_id", "title", "created_at"},
    "user_credits": {"owner", "credits", "created_at", "updated_at"},
}
NOT_NULL = {
    "threads": {"owner", "id", "facts", "summary", "summarized_through", "created_at", "updated_at"},
    "thread_turns": {"owner", "thread_id", "seq", "role", "text", "created_at"},
    "user_runs": {"owner", "run_id", "title", "created_at"},
    "user_credits": {"owner", "credits", "created_at", "updated_at"},
}
DEFAULTS = {
    "threads": {"title": None, "facts": {}, "summary": "", "summarized_through": 0},
    "thread_turns": {"tool_steps": None, "reply": None},
    "user_runs": {"thread_id": None},
    "user_credits": {"credits": 3},
}
UUID_COLUMNS = {"threads": {"id"}, "thread_turns": {"thread_id"}, "user_runs": {"thread_id"}, "user_credits": set()}
RESERVED = {"select", "order", "limit", "on_conflict"}



def pg_error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": "a Postgres message that must not be logged", "details": None})


class FakePostgrest:
    def __init__(self, secret: str = SECRET) -> None:
        self.secret = secret
        self.tables: dict[str, list[dict[str, Any]]] = {name: [] for name in PRIMARY_KEYS}
        self.requests: list[httpx.Request] = []
        self.down = False
        self.conflicts_on_turn_insert = 0  # answer the next N inserts into thread_turns with a duplicate-key 409

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---------- the endpoint ----------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("the project is not reachable")
        if request.headers.get("apikey") != self.secret:
            return httpx.Response(401, json={"message": "Invalid API key"})
        assert request.url.path.startswith("/rest/v1/"), request.url.path
        table = request.url.path[len("/rest/v1/") :]
        if table not in PRIMARY_KEYS:
            return pg_error(404, "42P01")
        params = dict(request.url.params.multi_items())
        prefer = request.headers.get("prefer", "")
        if request.method == "GET":
            return self.select(table, params)
        if request.method == "POST":
            return self.insert(table, params, prefer, json.loads(request.content))
        if request.method == "PATCH":
            return self.update(table, params, prefer, json.loads(request.content))
        raise AssertionError(f"the store made a call the fake does not know: {request.method} {table}")

    def filtered(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows = self.tables[table]
        for column, expr in params.items():
            if column in RESERVED:
                continue
            assert column in COLUMNS[table], f"filter on an unknown column {table}.{column}"
            if column in UUID_COLUMNS[table] and expr.startswith("eq."):
                uuid.UUID(expr[3:])  # Postgres would refuse a value that is not a uuid
            rows = [r for r in rows if self.matches(r.get(column), expr)]
        return rows

    @staticmethod
    def matches(value: Any, expr: str) -> bool:
        if expr.startswith("eq."):
            return str(value) == expr[3:]
        if expr.startswith("gt."):
            return value is not None and value > int(expr[3:])
        if expr == "not.is.null":
            return value is not None
        if expr.startswith("in.(") and expr.endswith(")"):
            return str(value) in expr[4:-1].split(",")
        raise AssertionError(f"a filter the fake does not know: {expr}")

    def select(self, table: str, params: dict[str, str]) -> httpx.Response:
        rows = list(self.filtered(table, params))
        for part in reversed([p for p in params.get("order", "").split(",") if p]):
            column, _, direction = part.partition(".")
            assert column in COLUMNS[table] and direction in ("asc", "desc"), part
            rows.sort(key=lambda r, c=column: r[c], reverse=direction == "desc")
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return httpx.Response(200, json=self.project(table, rows, params.get("select", "*")))

    @staticmethod
    def project(table: str, rows: list[dict[str, Any]], select: str) -> list[dict[str, Any]]:
        if select == "*":
            return json.loads(json.dumps(rows))
        wanted = select.split(",")
        assert set(wanted) <= COLUMNS[table], select
        return json.loads(json.dumps([{c: r.get(c) for c in wanted} for r in rows]))

    def insert(self, table: str, params: dict[str, str], prefer: str, body: Any) -> httpx.Response:
        incoming = body if isinstance(body, list) else [body]
        if table == "thread_turns" and self.conflicts_on_turn_insert > 0:
            self.conflicts_on_turn_insert -= 1
            return pg_error(409, "23505")
        ignore = "resolution=ignore-duplicates" in prefer
        if ignore:
            assert params.get("on_conflict") == ",".join(PRIMARY_KEYS[table]), params
        fresh: list[dict[str, Any]] = []
        for raw in incoming:
            if not set(raw) <= COLUMNS[table]:
                return pg_error(400, "PGRST204")
            row = {**json.loads(json.dumps(DEFAULTS[table])), **json.loads(json.dumps(raw))}
            if any(row.get(c) is None for c in NOT_NULL[table]):
                return pg_error(400, "23502")
            try:
                for column in UUID_COLUMNS[table]:
                    if row.get(column) is not None:
                        uuid.UUID(row[column])
            except ValueError:
                return pg_error(400, "22P02")
            if table == "thread_turns":
                if row["role"] not in ("user", "verity"):
                    return pg_error(400, "23514")
                if not any(t["owner"] == row["owner"] and t["id"] == row["thread_id"] for t in self.tables["threads"]):
                    return pg_error(409, "23503")  # the foreign key to threads
            key = tuple(row[c] for c in PRIMARY_KEYS[table])
            taken = any(tuple(r[c] for c in PRIMARY_KEYS[table]) == key for r in self.tables[table] + fresh)
            if taken:
                if ignore:
                    continue
                return pg_error(409, "23505")
            fresh.append(row)
        self.tables[table].extend(fresh)  # all or nothing, like one INSERT statement
        return httpx.Response(201)

    def update(self, table: str, params: dict[str, str], prefer: str, body: dict[str, Any]) -> httpx.Response:
        assert any(c not in RESERVED for c in params), "PATCH without a filter"
        if not set(body) <= COLUMNS[table]:
            return pg_error(400, "PGRST204")
        rows = self.filtered(table, params)
        if "owner" in body and table == "threads":
            for row in rows:  # one key already taken by the target stops the whole statement
                if any(t["owner"] == body["owner"] and t["id"] == row["id"] for t in self.tables["threads"]):
                    return pg_error(409, "23505")
            for row in rows:  # ON UPDATE CASCADE
                for turn in self.tables["thread_turns"]:
                    if turn["owner"] == row["owner"] and turn["thread_id"] == row["id"]:
                        turn["owner"] = body["owner"]
        for row in rows:
            row.update(json.loads(json.dumps(body)))
        if "return=representation" in prefer:
            return httpx.Response(200, json=self.project(table, rows, params.get("select", "*")))
        return httpx.Response(204)


# ---------- helpers ----------
def supabase_settings(tmp_path, **extra):  # noqa: ANN001, ANN201
    base = make_settings(tmp_path / "data")
    sb = SupabaseConfig(url=URL, secret_key=SecretStr(SECRET), publishable_key=SecretStr("sb_publishable_test"))
    return base.model_copy(update={"supabase": sb, "threads": base.threads.model_copy(update={"store": "auto"}), **extra})


@pytest.fixture()
def pg() -> FakePostgrest:
    return FakePostgrest()


@pytest.fixture()
async def store(tmp_path, pg):  # noqa: ANN001, ANN201
    s = SupabaseThreadStore(supabase_settings(tmp_path), transport=pg.transport())
    await s.open()
    yield s
    await s.close()


def turn(user: str, verity: str, steps: list | None = None, reply: dict | None = None) -> list[ThreadTurnIn]:
    return [ThreadTurnIn(role="user", text=user), ThreadTurnIn(role="verity", text=verity, steps=steps or [], reply=reply)]


def no_key_anywhere(pg: FakePostgrest) -> None:
    assert pg.requests, "no request was made"
    for request in pg.requests:
        assert request.headers["apikey"] == SECRET
        assert "authorization" not in request.headers  # the new secret keys are not JWTs: never a bearer token
        assert SECRET not in str(request.url) and SECRET.encode() not in request.content


# ---------- the same behaviour as the SQLite ThreadStore tests ----------
async def test_it_is_a_thread_store(store) -> None:
    assert isinstance(store, ThreadStore)


async def test_get_or_create_and_load(store, pg) -> None:
    assert await store.load("o1", TID) is None
    created = await store.get_or_create("o1", TID)
    assert created.id == TID and created.owner == "o1" and created.facts == {} and created.summary == "" and created.turns == []
    again = await store.get_or_create("o1", TID)
    assert again.created_at == created.created_at  # not created twice
    assert await store.load("o2", TID) is None  # another owner has no such thread
    other = await store.get_or_create("o2", TID)
    assert other.owner == "o2" and len(pg.tables["threads"]) == 2
    no_key_anywhere(pg)


async def test_facts_round_trip_and_unknown_threads_raise(store) -> None:
    await store.get_or_create("o1", TID)
    await store.update_facts("o1", TID, {"checked": True, "run_ids": ["r_1"], "knobs": {"width": 16}})
    got = await store.load("o1", TID)
    assert got is not None and got.facts == {"checked": True, "run_ids": ["r_1"], "knobs": {"width": 16}}
    with pytest.raises(NotFound):
        await store.update_facts("o1", T2, {})
    with pytest.raises(NotFound):
        await store.update_summary("o2", TID, "x", 2)
    with pytest.raises(NotFound):
        await store.append_turns("o2", TID, turn("a", "b"))


async def test_turns_are_numbered_in_pairs_capped_and_keep_their_steps(store) -> None:
    await store.get_or_create("o1", TID)
    steps = [{"tool": "list_blocks", "label": "Read the build list", "ok": True}]
    assert await store.append_turns("o1", TID, turn("u" * 500, "v" * 2_000, steps)) == 2
    assert await store.append_turns("o1", TID, turn("second", "answer")) == 4
    assert await store.append_turns("o1", TID, []) == 4  # nothing added: the seq stays
    got = await store.load("o1", TID)
    assert got is not None and [(t.seq, t.role) for t in got.turns] == [(1, "user"), (2, "verity"), (3, "user"), (4, "verity")]
    assert len(got.turns[0].text) == THREAD_USER_MAX == 300
    assert len(got.turns[1].text) == THREAD_VERITY_MAX == 1_200
    assert got.turns[1].steps == steps and got.turns[0].steps == []


async def test_since_summary_returns_only_the_rows_after_the_summary(store) -> None:
    await store.get_or_create("o1", TID)
    for n in range(1, 5):
        await store.append_turns("o1", TID, turn(f"q{n}", f"a{n}"))
    await store.update_summary("o1", TID, "The user asked four things.", 4)
    full = await store.load("o1", TID)
    recent = await store.load("o1", TID, since_summary=True)
    assert full is not None and recent is not None
    assert len(full.turns) == 8 and [t.seq for t in recent.turns] == [5, 6, 7, 8]
    assert recent.summary == "The user asked four things." and recent.summarized_through == 4
    assert [t.seq for t in (await store.get_or_create("o1", TID, since_summary=True)).turns] == [5, 6, 7, 8]


async def test_list_threads_is_owner_only_newest_first_and_titled_from_the_first_message(store) -> None:
    ids = [f"0000000{n}-2222-4333-8444-555555555555" for n in range(1, 4)]
    await store.get_or_create("o1", ids[0])  # no message: not listed
    for tid, text in ((ids[1], "first thread " + "y" * 100), (ids[2], "second thread")):
        await store.get_or_create("o1", tid)
        await store.update_facts("o1", tid, {"run_ids": ["r_a", "r_b"]})
        await store.append_turns("o1", tid, turn(text, "answer"))
        await store.append_turns("o1", tid, turn("later question", "later answer"))
        await asyncio.sleep(0.002)
    await store.get_or_create("o2", ids[1])
    await store.append_turns("o2", ids[1], turn("someone else's thread", "answer"))

    listed = await store.list_threads("o1")
    assert [t.id for t in listed] == [ids[2], ids[1]]  # newest first; the thread with no message is left out
    assert listed[1].title == ("first thread " + "y" * 100)[:60] and len(listed[1].title) == 60  # the first message, not the later one
    assert listed[0].title == "second thread" and listed[0].run_ids == ["r_a", "r_b"]
    assert [t.title for t in await store.list_threads("o2")] == ["someone else's thread"]
    assert len(await store.list_threads("o1", limit=1)) == 1
    assert await store.list_threads("nobody") == []


async def test_claim_moves_every_thread_of_one_owner_to_another(store) -> None:
    a, b = "0000000a-2222-4333-8444-555555555555", "0000000b-2222-4333-8444-555555555555"
    for tid in (a, b):
        await store.get_or_create("session", tid)
        await store.update_facts("session", tid, {"checked": True})
        await store.append_turns("session", tid, turn("hello " + tid[:8], "hi"))
    assert await store.claim("session", "user-1") == 2
    assert await store.load("session", a) is None
    moved = await store.load("user-1", a)
    assert moved is not None and moved.facts == {"checked": True} and [t.seq for t in moved.turns] == [1, 2]
    assert {t.id for t in await store.list_threads("user-1")} == {a, b}
    assert await store.list_threads("session") == []
    assert await store.claim("session", "user-1") == 0 and await store.claim("user-1", "user-1") == 0


async def test_claim_leaves_a_thread_the_target_already_has(store) -> None:
    await store.get_or_create("session", TID)
    await store.get_or_create("user-1", TID)
    await store.get_or_create("session", T2)
    assert await store.claim("session", "user-1") == 1  # only T2 moved
    assert await store.load("session", TID) is not None and await store.load("user-1", T2) is not None


# ---------- the run index and the stored reply ----------
async def test_user_runs_are_owner_only_newest_first_and_idempotent(store) -> None:
    await store.add_user_run("u:a", "r_1", TID, "Adder")
    await asyncio.sleep(0.002)
    await store.add_user_run("u:a", "r_2", None, "Counter")
    await store.add_user_run("u:a", "r_1", None, "ignored: the run is already listed")
    await store.add_user_run("u:b", "r_3", None, "Someone else's")
    mine = await store.list_user_runs("u:a")
    assert [(r.run_id, r.title, r.thread_id) for r in mine] == [("r_2", "Counter", None), ("r_1", "Adder", TID)]
    assert [r.run_id for r in await store.list_user_runs("u:a", limit=1)] == ["r_2"]
    assert [r.run_id for r in await store.list_user_runs("u:b")] == ["r_3"] and await store.list_user_runs("nobody") == []


async def test_the_wire_reply_of_a_verity_turn_is_kept_up_to_32_kb(store, pg) -> None:
    await store.get_or_create("o1", TID)
    reply = {"check": {"result": "in_range", "family": "add8"}, "reply": {"kind": "answer", "text": "hi", "toolSteps": []}}
    await store.append_turns("o1", TID, turn("a 16-bit adder", "hi", reply=reply))
    big = {"check": None, "reply": {"kind": "answer", "text": "x" * (THREAD_REPLY_MAX_BYTES + 10)}}
    await store.append_turns("o1", TID, turn("again", "hi", reply=big))
    await store.append_turns("o1", TID, [ThreadTurnIn(role="user", text="u", reply=reply), ThreadTurnIn(role="verity", text="v")])
    got = await store.load("o1", TID)
    assert got is not None
    assert [t.reply for t in got.turns] == [None, reply, None, None, None, None]  # the user row never keeps one; the big one is dropped
    assert got.turns[3].text == "hi"  # its text and steps are still there
    no_key_anywhere(pg)


async def test_a_second_writer_that_took_the_seq_is_renumbered_once(store, pg) -> None:
    await store.get_or_create("o1", TID)
    pg.conflicts_on_turn_insert = 1
    assert await store.append_turns("o1", TID, turn("q", "a")) == 2  # the 409 was retried
    pg.conflicts_on_turn_insert = 2
    with pytest.raises(StoreUnavailable):
        await store.append_turns("o1", TID, turn("q", "a"))


# ---------- when Supabase is not there ----------
async def test_an_unreachable_project_is_store_unavailable_and_the_log_has_no_key(store, pg, caplog) -> None:
    pg.down = True
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(StoreUnavailable, match="Saved chats are unreachable right now."):
            await store.load("o1", TID)
        with pytest.raises(StoreUnavailable):
            await store.list_user_runs("o1")
    assert "Supabase GET threads failed: ConnectError" in caplog.text
    assert SECRET not in caplog.text and "sb_secret" not in caplog.text


async def test_a_refused_call_logs_the_status_and_the_postgres_code_only(tmp_path, pg, caplog) -> None:
    wrong = SupabaseThreadStore(supabase_settings(tmp_path), transport=FakePostgrest(secret="another-key").transport())
    await wrong.open()
    with caplog.at_level(logging.DEBUG), pytest.raises(StoreUnavailable):
        await wrong.load("o1", TID)
    assert "answered 401" in caplog.text and SECRET not in caplog.text
    await wrong.close()

    s = SupabaseThreadStore(supabase_settings(tmp_path), transport=pg.transport())
    await s.open()
    caplog.clear()
    with caplog.at_level(logging.DEBUG), pytest.raises(StoreUnavailable):
        await s.get_or_create("o1", "not-a-uuid")  # Postgres: 22P02
    assert "answered 400 22P02" in caplog.text and "must not be logged" not in caplog.text
    await s.close()


async def test_an_answer_that_is_not_json_or_has_the_wrong_shape_is_store_unavailable(tmp_path) -> None:
    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": TID}])  # no owner, created_at, ...

    for handler in (garbage, wrong_shape):
        s = SupabaseThreadStore(supabase_settings(tmp_path), transport=httpx.MockTransport(handler))
        await s.open()
        with pytest.raises(StoreUnavailable):
            await s.load("o1", TID)
        await s.close()


def test_the_store_needs_both_the_url_and_the_secret_key(tmp_path) -> None:
    base = make_settings(tmp_path / "data")
    with pytest.raises(ValueError):
        SupabaseThreadStore(base)
    only_url = base.model_copy(update={"supabase": SupabaseConfig(url=URL)})
    with pytest.raises(ValueError):
        SupabaseThreadStore(only_url)


# ---------- the SQL file matches what the store sends ----------
def test_schema_sql_has_the_tables_the_store_uses_and_no_policies() -> None:
    sql = SCHEMA.read_text(encoding="utf-8").lower()
    assert "sql editor" in sql  # the header says where to paste it
    for table, columns in COLUMNS.items():
        assert f"create table if not exists public.{table}" in sql
        body = sql.split(f"create table if not exists public.{table}", 1)[1].split(");", 1)[0]
        for column in columns:
            assert f"\n  {column} " in body, f"{table}.{column}"
        assert f"alter table public.{table} " in sql and "enable row level security" in sql
    assert "create policy" not in sql
    assert "revoke all on public.threads, public.thread_turns, public.user_runs, public.user_credits from anon, authenticated" in sql
    assert "grant select, insert, update, delete on public.threads, public.thread_turns, public.user_runs, public.user_credits to service_role" in sql

    assert "role        text        not null check (role in ('user', 'verity'))".lower() in sql
    assert "on update cascade on delete cascade" in sql
    assert "primary key (owner, thread_id, seq)" in sql and "primary key (owner, run_id)" in sql and "primary key (owner, id)" in sql
    assert chr(0x2014) not in sql and chr(0x2013) not in sql  # no em dash or en dash


# ---------- through the real app ----------
ME = anonymous_sid("testclient")  # the session of a TestClient when auth is off


def client_for(tmp_path, packs, pg: FakePostgrest, *, settings=None):  # noqa: ANN001, ANN201
    settings = settings or supabase_settings(tmp_path)
    provider = FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings))
    runs = SqliteStore(settings)
    chats = SupabaseThreadStore(settings, transport=pg.transport())
    research = ResearchService(settings, runs, provider, FakeSearchAdapter(), live_families(packs))
    manager = RunManager(settings, runs, provider, FakeToolRunner(delay_s=0.2), packs, research=research, threads=chats)
    return TestClient(create_app(settings, manager, startup=[runs.open, chats.open], shutdown=[runs.close, chats.close]))


def post(client: TestClient, text: str, thread: str = TID, **extra):  # noqa: ANN003, ANN201
    return client.post(f"/threads/{thread}/messages", json={"text": text, **extra})


def test_chats_live_in_the_supabase_store_and_survive_a_restart(tmp_path, packs, pg) -> None:
    with client_for(tmp_path, packs, pg) as client:
        first = post(client, "a 16-bit adder").json()
        assert first["check"]["verdict"] == "buildable_now"
        post(client, "make it faster")
        assert client.get("/threads").json()["threads"][0]["title"] == "a 16-bit adder"
    assert len(pg.tables["thread_turns"]) == 4 and {r["owner"] for r in pg.tables["thread_turns"]} == {ME}
    assert pg.tables["thread_turns"][1]["reply"]["check"]["verdict"] == "buildable_now"
    assert pg.tables["thread_turns"][3]["reply"]["check"] is None  # the second message ran no fact check
    with client_for(tmp_path, packs, pg) as again:  # a new process: the chats come back from "Supabase"
        view = again.get(f"/threads/{TID}").json()
        assert [t["role"] for t in view["turns"]] == ["user", "verity", "user", "verity"]
        assert view["turns"][1]["check"]["verdict"] == "buildable_now" and view["turns"][1]["reply"]["kind"] == "answer"
        assert "check" not in view["turns"][3] and view["turns"][3]["reply"]["kind"] == "proposal"
        assert post(again, "and a bit wider?").json()["check"] is None  # still fact-checked
    no_key_anywhere(pg)


def test_a_run_goes_into_the_run_index_and_runs_mine_lists_it(tmp_path, packs, pg) -> None:
    with client_for(tmp_path, packs, pg) as client:
        post(client, "a 16-bit adder")
        started = client.post("/runs", json={"presetId": "add8", "priority": "speed", "mode": "fast", "params": {"width": 16}, "threadId": TID})
        assert started.status_code == 201
        run_id = started.json()["runId"]
        assert pg.tables["user_runs"][0]["owner"] == ME and pg.tables["user_runs"][0]["thread_id"] == TID
        mine = client.get("/runs/mine")
        assert mine.status_code == 200 and mine.headers["cache-control"] == "no-store"
        [item] = mine.json()["runs"]
        assert set(item) == {"id", "title", "threadId", "createdAt", "status"}
        assert item["id"] == run_id and item["threadId"] == TID and item["createdAt"].endswith("Z") and item["status"] in ("queued", "running")
        client.post(f"/runs/{run_id}/stop")
        pg.tables["user_runs"].append(  # an older run that did not start from a thread
            {"owner": ME, "run_id": "r_old", "thread_id": None, "title": "Older", "created_at": "2026-10-01T00:00:00.000000+00:00"}
        )
        listed = client.get("/runs/mine").json()["runs"]
        assert [r["id"] for r in listed] == [run_id, "r_old"]  # newest first
        assert listed[1]["threadId"] is None and "threadId" in listed[1]  # null on the wire, not left out


def test_a_missing_run_file_shows_a_null_status(tmp_path, packs, pg) -> None:
    pg.tables["user_runs"].append({"owner": ME, "run_id": "r_gone", "thread_id": None, "title": "Old run", "created_at": "2026-10-01T00:00:00.000000+00:00"})
    with client_for(tmp_path, packs, pg) as client:
        assert client.get("/runs/mine").json() == {"runs": [{"id": "r_gone", "title": "Old run", "threadId": None, "createdAt": "2026-10-01T00:00:00Z", "status": None}]}


def test_supabase_down_is_a_502_with_a_plain_sentence_and_a_run_still_starts(tmp_path, packs, pg, caplog) -> None:
    with client_for(tmp_path, packs, pg) as client:
        post(client, "a 16-bit adder")
        pg.down = True
        for res in (post(client, "make it faster"), client.get("/threads"), client.get(f"/threads/{TID}"), client.get("/runs/mine")):
            assert res.status_code == 502 and res.json() == {"error": "Saved chats are unreachable right now."}
        with caplog.at_level(logging.DEBUG):
            started = client.post("/runs", json={"presetId": "add8", "priority": "area", "mode": "fast"})
        assert started.status_code == 201  # the run list is a convenience: its failure never fails the run
        assert "could not add it to the run list" in caplog.text and SECRET not in caplog.text
        client.post(f"/runs/{started.json()['runId']}/stop")
        pg.down = False
        assert client.get("/threads").status_code == 200  # and it recovers by itself


def test_the_thread_store_follows_the_settings(tmp_path) -> None:
    local = make_settings(tmp_path / "data")
    assert local.threads_store() == "sqlite"
    assert supabase_settings(tmp_path).threads_store() == "supabase"
    forced = supabase_settings(tmp_path, threads=local.threads.model_copy(update={"store": "sqlite"}))
    assert forced.threads_store() == "sqlite"
    url_only = local.model_copy(update={"supabase": SupabaseConfig(url=URL)})
    assert url_only.threads_store() == "sqlite" and url_only.supabase_auth_enabled()  # sign-in works, chats stay local
