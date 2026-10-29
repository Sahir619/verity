"""SupabaseThreadStore: core.protocols.ThreadStore over Supabase Postgres (backend ARCHITECTURE 17.5).

Chats (`threads`, `thread_turns`) and each user's list of runs (`user_runs`) live in the project's Postgres; run files,
events and spend ledgers stay in SqliteStore and on disk. Tables and grants: backend/supabase/schema.sql.

The store speaks to the Data API (PostgREST, https://<ref>.supabase.co/rest/v1/<table>) over one httpx.AsyncClient. The
secret key goes in the `apikey` header ONLY: the new sb_secret_ keys are not JWTs, so there is no Authorization header.
The key bypasses row level security (the tables have no policies, and anon and authenticated have no grants), and
every query here names its owner, so one owner never reads or writes another's rows.

A call that cannot be made, or that Supabase refuses, raises StoreUnavailable (the API answers 502 "Saved chats are
unreachable right now."). The cause is logged as a status and a Postgres error code, never a key, a header or a body.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from ..config import Settings
from ..core.errors import NotFound, StoreUnavailable
from ..core.records import (
    THREAD_USER_MAX,
    THREAD_VERITY_MAX,
    ThreadData,
    ThreadListItem,
    ThreadTurnIn,
    ThreadTurnRow,
    UserRunRow,
    capped_reply,
    thread_title,
)

log = logging.getLogger("verity.store.supabase")

_TURN_COLUMNS = "seq,role,text,tool_steps,reply,created_at"
_MAX_TURN_ROWS = 1_000  # PostgREST's default row cap; a thread this long has folded its older turns long ago


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _eq(value: str) -> str:
    return f"eq.{value}"


def _json_safe(value: Any) -> Any:
    """`value` as plain JSON data, the way SqliteStore writes it (anything else becomes its string)."""
    return json.loads(json.dumps(value, default=str))


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class SupabaseThreadStore:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        sb = settings.supabase
        if not settings.supabase_auth_enabled() or not settings.has_supabase_secret() or sb.secret_key is None:
            raise ValueError("the Supabase thread store needs SUPABASE_URL and SUPABASE_SECRET_KEY")
        self._base = f"{(sb.url or '').strip().rstrip('/')}/rest/v1"
        self._key = sb.secret_key.get_secret_value()
        self._timeout = sb.timeout_s
        self._transport = transport  # tests pass an httpx.MockTransport
        self._http: httpx.AsyncClient | None = None

    # ---------- lifecycle ----------
    async def open(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                headers={"apikey": self._key, "Accept": "application/json"},
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
            )

    async def close(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()

    # ---------- HTTP ----------
    async def _send(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("store is not open: call await store.open() first")
        try:
            return await self._http.request(
                method,
                f"{self._base}/{table}",
                params=params,
                json=body,
                headers={"Prefer": prefer} if prefer else None,
            )
        except httpx.HTTPError as exc:
            log.warning("Supabase %s %s failed: %s", method, table, type(exc).__name__)
            raise StoreUnavailable from None

    @staticmethod
    def _check(response: httpx.Response, method: str, table: str) -> httpx.Response:
        if response.status_code >= 400:
            code = ""
            try:
                body = response.json()
                code = str(body.get("code", ""))[:16] if isinstance(body, dict) else ""
            except ValueError:
                pass
            log.warning("Supabase %s %s answered %s %s", method, table, response.status_code, code)
            raise StoreUnavailable
        return response

    async def _call(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        """The JSON of the answer (None when it has no body)."""
        response = self._check(await self._send(method, table, params=params, body=body, prefer=prefer), method, table)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            log.warning("Supabase %s %s answered something that is not JSON", method, table)
            raise StoreUnavailable from None

    # ---------- threads ----------
    @staticmethod
    def _turn(row: dict[str, Any]) -> ThreadTurnRow:
        steps = row.get("tool_steps")
        reply = row.get("reply")
        return ThreadTurnRow(
            seq=int(row["seq"]),
            role=row["role"],
            text=row["text"],
            steps=[s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else [],
            reply=reply if isinstance(reply, dict) else None,
            created_at=_dt(row["created_at"]),
        )

    async def load(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData | None:
        rows = await self._call(
            "GET", "threads", params={"owner": _eq(owner), "id": _eq(thread_id), "select": "*", "limit": "1"}
        )
        found = _dicts(rows)
        if not found:
            return None
        row = found[0]
        try:
            through = int(row["summarized_through"])
            turn_rows = await self._call(
                "GET",
                "thread_turns",
                params={
                    "owner": _eq(owner),
                    "thread_id": _eq(thread_id),
                    "seq": f"gt.{through if since_summary else 0}",
                    "order": "seq.asc",
                    "select": _TURN_COLUMNS,
                    "limit": str(_MAX_TURN_ROWS),
                },
            )
            facts = row.get("facts")
            return ThreadData(
                owner=row["owner"],
                id=row["id"],
                created_at=_dt(row["created_at"]),
                updated_at=_dt(row["updated_at"]),
                facts=facts if isinstance(facts, dict) else {},
                summary=row.get("summary") or "",
                summarized_through=through,
                turns=[self._turn(r) for r in _dicts(turn_rows)],
            )
        except (KeyError, TypeError, ValueError):
            log.warning("Supabase threads answered a row of an unexpected shape")
            raise StoreUnavailable from None

    async def get_or_create(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData:
        now = _now()
        await self._call(
            "POST",
            "threads",
            params={"on_conflict": "owner,id"},
            body={"owner": owner, "id": thread_id, "facts": {}, "created_at": now, "updated_at": now},
            prefer="resolution=ignore-duplicates,return=minimal",
        )
        data = await self.load(owner, thread_id, since_summary=since_summary)
        if data is None:  # the row was just inserted, or was there
            raise StoreUnavailable
        return data

    async def _patch_thread(self, owner: str, thread_id: str, values: dict[str, Any]) -> None:
        rows = await self._call(
            "PATCH",
            "threads",
            params={"owner": _eq(owner), "id": _eq(thread_id), "select": "id"},
            body={**values, "updated_at": _now()},
            prefer="return=representation",
        )
        if not _dicts(rows):
            raise NotFound(thread_id)

    async def update_facts(self, owner: str, thread_id: str, facts: dict[str, Any]) -> None:
        await self._patch_thread(owner, thread_id, {"facts": _json_safe(facts)})

    async def update_summary(self, owner: str, thread_id: str, summary: str, through_seq: int) -> None:
        await self._patch_thread(owner, thread_id, {"summary": summary, "summarized_through": through_seq})

    async def _last_seq(self, owner: str, thread_id: str) -> int:
        rows = _dicts(
            await self._call(
                "GET",
                "thread_turns",
                params={
                    "owner": _eq(owner),
                    "thread_id": _eq(thread_id),
                    "select": "seq",
                    "order": "seq.desc",
                    "limit": "1",
                },
            )
        )
        try:
            return int(rows[0]["seq"]) if rows else 0
        except (KeyError, TypeError, ValueError):
            raise StoreUnavailable from None

    async def append_turns(self, owner: str, thread_id: str, turns: Sequence[ThreadTurnIn]) -> int:
        """Add the rows in one request, cut to THREAD_USER_MAX and THREAD_VERITY_MAX characters. Returns the seq of the
        last row (the current one when `turns` is empty). Raises NotFound for a thread that does not exist. The agent
        holds one lock per thread, so two writers do not meet; if they ever do, the primary key refuses the second
        and the rows are numbered again once."""
        found = _dicts(
            await self._call(
                "GET", "threads", params={"owner": _eq(owner), "id": _eq(thread_id), "select": "id,title", "limit": "1"}
            )
        )
        if not found:
            raise NotFound(thread_id)
        now = _now()
        seq = await self._last_seq(owner, thread_id)
        if turns:
            for attempt in (1, 2):
                rows = [
                    {
                        "owner": owner,
                        "thread_id": thread_id,
                        "seq": seq + n,
                        "role": turn.role,
                        "text": turn.text[: THREAD_USER_MAX if turn.role == "user" else THREAD_VERITY_MAX],
                        "tool_steps": _json_safe(turn.steps) if turn.steps else None,
                        "reply": _json_safe(capped_reply(turn.reply)) if turn.role == "verity" else None,
                        "created_at": now,
                    }
                    for n, turn in enumerate(turns, start=1)
                ]
                response = await self._send("POST", "thread_turns", body=rows, prefer="return=minimal")
                if response.status_code == 409 and attempt == 1:
                    seq = await self._last_seq(owner, thread_id)
                    continue
                self._check(response, "POST", "thread_turns")
                seq += len(turns)
                break
        values: dict[str, Any] = {}
        if found[0].get("title") is None:
            first = next((t for t in turns if t.role == "user"), None)
            if first is not None:
                values["title"] = thread_title(first.text[:THREAD_USER_MAX])
        await self._patch_thread(owner, thread_id, values)
        return seq

    async def list_threads(self, owner: str, limit: int = 50) -> list[ThreadListItem]:
        """This owner's threads that have at least one message (a title is set with the first one), newest first."""
        rows = await self._call(
            "GET",
            "threads",
            params={
                "owner": _eq(owner),
                "title": "not.is.null",
                "order": "updated_at.desc,id.asc",
                "limit": str(max(0, limit)),
                "select": "id,title,updated_at,facts",
            },
        )
        out: list[ThreadListItem] = []
        try:
            for row in _dicts(rows):
                facts = row.get("facts")
                run_ids = facts.get("run_ids", []) if isinstance(facts, dict) else []
                out.append(
                    ThreadListItem(
                        id=row["id"],
                        title=row["title"],
                        updated_at=_dt(row["updated_at"]),
                        run_ids=[r for r in run_ids if isinstance(r, str)] if isinstance(run_ids, list) else [],
                    )
                )
        except (KeyError, TypeError, ValueError):
            log.warning("Supabase threads answered a row of an unexpected shape")
            raise StoreUnavailable from None
        return out

    async def claim(self, from_owner: str, to_owner: str) -> int:
        """Move every thread of one owner to another (its turns follow: the key cascades on update). A thread id the
        target already has stays where it is. Returns how many moved."""
        if from_owner == to_owner:
            return 0
        mine = {r.get("id") for r in _dicts(await self._call("GET", "threads", params={"owner": _eq(from_owner), "select": "id"}))}
        theirs = {r.get("id") for r in _dicts(await self._call("GET", "threads", params={"owner": _eq(to_owner), "select": "id"}))}
        movable = sorted(i for i in mine - theirs if isinstance(i, str))
        if not movable:
            return 0
        rows = await self._call(
            "PATCH",
            "threads",
            params={"owner": _eq(from_owner), "id": f"in.({','.join(movable)})", "select": "id"},
            body={"owner": to_owner},
            prefer="return=representation",
        )
        return len(_dicts(rows))

    # ---------- run index ----------
    async def add_user_run(self, owner: str, run_id: str, thread_id: str | None, title: str) -> None:
        await self._call(
            "POST",
            "user_runs",
            params={"on_conflict": "owner,run_id"},
            body={"owner": owner, "run_id": run_id, "thread_id": thread_id, "title": title, "created_at": _now()},
            prefer="resolution=ignore-duplicates,return=minimal",
        )

    async def list_user_runs(self, owner: str, limit: int = 50) -> list[UserRunRow]:
        rows = await self._call(
            "GET",
            "user_runs",
            params={
                "owner": _eq(owner),
                "order": "created_at.desc,run_id.asc",
                "limit": str(max(0, limit)),
                "select": "run_id,thread_id,title,created_at",
            },
        )
        try:
            return [
                UserRunRow(run_id=r["run_id"], title=r["title"], thread_id=r.get("thread_id"), created_at=_dt(r["created_at"]))
                for r in _dicts(rows)
            ]
        except (KeyError, TypeError, ValueError):
            log.warning("Supabase user_runs answered a row of an unexpected shape")
            raise StoreUnavailable from None

    # ---------- user credits ----------
    async def get_credits(self, owner: str, default: int = 3) -> int:
        rows = await self._call(
            "GET",
            "user_credits",
            params={"owner": _eq(owner), "select": "credits"},
        )
        dicts = _dicts(rows)
        if dicts:
            try:
                return int(dicts[0]["credits"])
            except (KeyError, TypeError, ValueError):
                pass
        now = _now()
        await self._call(
            "POST",
            "user_credits",
            params={"on_conflict": "owner"},
            body={"owner": owner, "credits": default, "created_at": now, "updated_at": now},
            prefer="resolution=ignore-duplicates,return=minimal",
        )
        rows2 = await self._call(
            "GET",
            "user_credits",
            params={"owner": _eq(owner), "select": "credits"},
        )
        dicts2 = _dicts(rows2)
        if dicts2:
            try:
                return int(dicts2[0]["credits"])
            except (KeyError, TypeError, ValueError):
                pass
        return default

    async def deduct_credit(self, owner: str, default: int = 3) -> int:
        cur = await self.get_credits(owner, default)
        if cur <= 0:
            return -1
        new_credits = cur - 1
        now = _now()
        await self._call(
            "PATCH",
            "user_credits",
            params={"owner": _eq(owner)},
            body={"credits": new_credits, "updated_at": now},
            prefer="return=minimal",
        )
        return new_credits

    async def set_credits(
        self, owner: str, credits: int, email: str | None = None, role: str | None = None
    ) -> None:
        now = _now()
        body: dict[str, Any] = {
            "owner": owner,
            "credits": credits,
            "created_at": now,
            "updated_at": now,
            "role": role or "user",
        }
        if email is not None:
            body["email"] = email
        await self._call(
            "POST",
            "user_credits",
            params={"on_conflict": "owner"},
            body=body,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    async def list_user_credits(self) -> list[dict[str, Any]]:
        rows = await self._call(
            "GET",
            "user_credits",
            params={"select": "owner,credits,email,role,created_at,updated_at", "order": "created_at.desc"},
        )
        dicts = _dicts(rows)
        out: list[dict[str, Any]] = []
        for d in dicts:
            out.append(
                {
                    "owner": str(d.get("owner", "")),
                    "credits": int(d.get("credits", 0)),
                    "email": d.get("email"),
                    "role": str(d.get("role") or "user"),
                    "runs_count": 0,
                    "total_spent_usd": 0.0,
                    "created_at": d.get("created_at"),
                    "updated_at": d.get("updated_at"),
                }
            )
        return out

