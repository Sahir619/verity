"""Migration 6 (threads) and the ThreadStore methods of SqliteStore: caps, turn numbering, summary, listing, claim."""

import sqlite3

import pytest

from verity.core.errors import NotFound
from verity.core.protocols import ThreadStore
from verity.core.records import THREAD_REPLY_MAX_BYTES, THREAD_USER_MAX, THREAD_VERITY_MAX, ThreadTurnIn
from verity.store import SqliteStore
from verity.store.migrations import MIGRATIONS
from verity_test_support import make_settings

TID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture()
async def store(settings):
    s = SqliteStore(settings)
    await s.open()
    yield s
    await s.close()


def turn(user: str, verity: str, steps: list | None = None) -> list[ThreadTurnIn]:
    return [ThreadTurnIn(role="user", text=user), ThreadTurnIn(role="verity", text=verity, steps=steps or [])]


async def test_a_version_5_database_migrates_to_the_latest_and_keeps_its_data(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = sqlite3.connect(settings.data_dir / "verity.db")
    for script in MIGRATIONS[:5]:
        db.executescript(script)
    db.execute("INSERT INTO session_spend (session_id, day, usd) VALUES ('s1', '2026-10-01', 0.5)")
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    db.close()

    store = SqliteStore(settings)
    await store.open()
    try:
        assert len(MIGRATIONS) == 10  # 6 threads, 7 run list, 8 user_credits, 9 user_credits email/role, 10 cpu_jobs
        row = await store._fetchone("PRAGMA user_version")
        assert row is not None and row[0] == 10
        assert await store.session_spend("s1", "2026-10-01") == 0.5
        tables = {r[0] for r in await store._fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"threads", "thread_turns"} <= tables
        threads = {r[1]: r for r in await store._fetchall("PRAGMA table_info(threads)")}
        assert list(threads) == ["owner", "id", "created_at", "updated_at", "facts_json", "summary", "summarized_through"]
        turns = {r[1] for r in await store._fetchall("PRAGMA table_info(thread_turns)")}
        assert turns == {"owner", "thread_id", "seq", "role", "text", "steps_json", "created_at", "reply_json"}
        # the role column only takes user or verity
        with pytest.raises(sqlite3.IntegrityError):
            await store._write(
                "INSERT INTO thread_turns (owner, thread_id, seq, role, text, created_at) VALUES ('o', 't', 1, 'model', 'x', 'now')"
            )
    finally:
        await store.close()
    again = SqliteStore(settings)  # re-opening runs nothing new
    await again.open()
    await again.close()


async def test_sqlite_store_is_a_thread_store(store) -> None:
    assert isinstance(store, ThreadStore)


async def test_get_or_create_and_load(store) -> None:
    assert await store.load("o1", TID) is None
    created = await store.get_or_create("o1", TID)
    assert created.id == TID and created.owner == "o1" and created.facts == {} and created.summary == "" and created.turns == []
    again = await store.get_or_create("o1", TID)
    assert again.created_at == created.created_at  # not created twice
    assert await store.load("o2", TID) is None  # another owner has no such thread
    other = await store.get_or_create("o2", TID)
    assert other.owner == "o2"


async def test_facts_round_trip_and_unknown_threads_raise(store) -> None:
    await store.get_or_create("o1", TID)
    await store.update_facts("o1", TID, {"checked": True, "run_ids": ["r_1"], "knobs": {"width": 16}})
    got = await store.load("o1", TID)
    assert got is not None and got.facts == {"checked": True, "run_ids": ["r_1"], "knobs": {"width": 16}}
    with pytest.raises(NotFound):
        await store.update_facts("o1", "22222222-2222-4333-8444-555555555555", {})
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
    await store.get_or_create("o2", ids[1])
    await store.append_turns("o2", ids[1], turn("someone else's thread", "answer"))

    listed = await store.list_threads("o1")
    assert [t.id for t in listed] == [ids[2], ids[1]]  # newest first; the thread with no message is left out
    assert listed[1].title == ("first thread " + "y" * 100)[:60] and len(listed[1].title) == 60
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


# ---------- migration 7: the run index and the stored reply ----------
async def test_a_version_6_database_migrates_to_7_and_keeps_its_threads(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = sqlite3.connect(settings.data_dir / "verity.db")
    for script in MIGRATIONS[:6]:
        db.executescript(script)
    when = "2026-10-01T00:00:00+00:00"
    db.execute("INSERT INTO threads (owner, id, created_at, updated_at, facts_json) VALUES ('o1', ?, ?, ?, '{}')", (TID, when, when))
    db.execute("INSERT INTO thread_turns (owner, thread_id, seq, role, text, created_at) VALUES ('o1', ?, 2, 'verity', 'old', ?)", (TID, when))
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    db.close()

    store = SqliteStore(settings)
    await store.open()
    try:
        row = await store._fetchone("PRAGMA user_version")
        assert row is not None and row[0] == len(MIGRATIONS)
        columns = {r[1]: r for r in await store._fetchall("PRAGMA table_info(user_runs)")}
        assert list(columns) == ["owner", "run_id", "thread_id", "title", "created_at"]
        assert [r[1] for r in await store._fetchall("PRAGMA table_info(thread_turns)")][-2:] == ["created_at", "reply_json"]
        old = await store.load("o1", TID)
        assert old is not None and [(t.seq, t.text, t.reply) for t in old.turns] == [(2, "old", None)]  # an older row has no reply
        await store.add_user_run("o1", "r_1", TID, "Adder")
        assert [r.run_id for r in await store.list_user_runs("o1")] == ["r_1"]
    finally:
        await store.close()


async def test_a_version_7_database_migrates_to_8_and_gets_user_credits(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = sqlite3.connect(settings.data_dir / "verity.db")
    for script in MIGRATIONS[:7]:
        db.executescript(script)
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 7
    db.close()

    store = SqliteStore(settings)
    await store.open()
    try:
        row = await store._fetchone("PRAGMA user_version")
        assert row is not None and row[0] == len(MIGRATIONS)
        columns = {r[1]: r for r in await store._fetchall("PRAGMA table_info(user_credits)")}
        assert list(columns)[:4] == ["owner", "credits", "created_at", "updated_at"]  # 9 adds email and role
        assert await store.get_credits("user_a") == 3
        assert await store.deduct_credit("user_a") == 2
        await store.set_credits("user_a", 10)
        assert await store.get_credits("user_a") == 10
    finally:
        await store.close()
    again = SqliteStore(settings)  # re-opening runs nothing new
    await again.open()
    await again.close()


async def test_user_runs_are_owner_only_newest_first_and_idempotent(store) -> None:
    await store.add_user_run("u:a", "r_1", TID, "Adder")
    await store.add_user_run("u:a", "r_2", None, "Counter")
    await store.add_user_run("u:a", "r_1", None, "ignored: the run is already listed")
    await store.add_user_run("u:b", "r_3", None, "Someone else's")
    mine = await store.list_user_runs("u:a")
    assert [(r.run_id, r.title, r.thread_id) for r in mine] == [("r_2", "Counter", None), ("r_1", "Adder", TID)]
    assert [r.run_id for r in await store.list_user_runs("u:a", limit=1)] == ["r_2"]
    assert [r.run_id for r in await store.list_user_runs("u:b")] == ["r_3"] and await store.list_user_runs("nobody") == []


async def test_the_wire_reply_of_a_verity_turn_is_kept_up_to_32_kb(store) -> None:
    await store.get_or_create("o1", TID)
    reply = {"check": {"result": "in_range", "family": "add8"}, "reply": {"kind": "answer", "text": "hi", "toolSteps": []}}
    await store.append_turns("o1", TID, turn("a 16-bit adder", "hi") + [])
    await store.append_turns(
        "o1", TID, [ThreadTurnIn(role="user", text="u", reply=reply), ThreadTurnIn(role="verity", text="v", reply=reply)]
    )
    big = {"check": None, "reply": {"kind": "answer", "text": "x" * (THREAD_REPLY_MAX_BYTES + 10)}}
    await store.append_turns("o1", TID, [ThreadTurnIn(role="user", text="u"), ThreadTurnIn(role="verity", text="v", reply=big)])
    got = await store.load("o1", TID)
    assert got is not None
    assert [t.reply for t in got.turns] == [None, None, None, reply, None, None]  # a user row never keeps one; the big one is dropped
    assert got.turns[5].text == "v"  # its text is still there
