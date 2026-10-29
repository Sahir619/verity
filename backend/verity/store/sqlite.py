"""SqliteStore: implements core.protocols.Store (ARCHITECTURE 7 and 15).

One aiosqlite connection, WAL mode. Writes are serialized by an asyncio lock; reads are not. All SQL uses
bound parameters; the only interpolated pieces are column names and placeholder counts taken from static
tables in this file, never from input.
"""

import asyncio
import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from ..config import Settings
from ..contracts import (
    Attempt,
    Candidate,
    EventBase,
    RunOutcomeSummary,
    RunSignoffSummary,
    RunSummaryItem,
    RunWinnerSummary,
    event_to_json,
    parse_event_json,
    to_json,
)
from ..contracts.signoff import Signoff
from ..core.budget import BudgetLimits, SpendSnapshot, spend_owner
from ..core.errors import NotFound
from ..core.records import (
    ArtifactRef,
    AttemptMeta,
    CandidateRow,
    EvalResultRow,
    ResearchRecord,
    ResearchSourceRow,
    RunOutcomeRow,
    RunPatch,
    RunRecord,
    RunStatus,
    StrategyOutcomeRow,
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
from .files import ArtifactFiles, sha256_hex
from .migrations import MIGRATIONS

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SIGNOFF_FILE = "final/signoff.json"  # the winner's sign-off sheet, written once per run (orchestrator/signoff.py)


class DuplicateEvent(ValueError):
    """append_event was called with a (run_id, seq) that already exists (the events table is append-only)."""


# ---------- value helpers ----------
def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _iso_opt(value: datetime | None) -> str | None:
    return None if value is None else _iso(value)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _dt_opt(value: str | None) -> datetime | None:
    return None if value is None else _dt(value)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _now_iso() -> str:
    return _iso(datetime.now(UTC))


# RunPatch field -> (column, converter). Column names come from this static table only.
_PATCH_COLUMNS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "status": ("status", lambda v: v),
    "started_at": ("started_at", _iso_opt),
    "ended_at": ("ended_at", _iso_opt),
    "heartbeat_at": ("heartbeat_at", _iso_opt),
    "spent": ("spent_json", lambda v: v.model_dump_json()),
    "tool_image_digest": ("tool_image_digest", lambda v: v or ""),
    "models": ("models_json", lambda v: _json(v)),
    "prompt_versions": ("prompt_versions_json", lambda v: _json(v)),
    "error_reason": ("error_reason", lambda v: v),
    "winner_id": ("winner_id", lambda v: v),
    "title": ("title", lambda v: v),
    "params": ("params_json", lambda v: _json(v) if v is not None else None),
}


# Columns declared NOT NULL: an explicit None in the patch means "leave unchanged".
_NOT_NULLABLE = frozenset({"status", "spent", "models", "prompt_versions", "tool_image_digest"})


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    keys = row.keys()
    params = json.loads(row["params_json"]) if "params_json" in keys and row["params_json"] else None
    title = row["title"] if "title" in keys else None
    research_id = row["research_id"] if "research_id" in keys else None
    return RunRecord(
        id=row["id"],
        session_id=row["session_id"],
        preset_id=row["preset_id"],
        priority=row["priority"],
        mode=row["mode"],
        status=row["status"],
        created_at=_dt(row["created_at"]),
        started_at=_dt_opt(row["started_at"]),
        ended_at=_dt_opt(row["ended_at"]),
        heartbeat_at=_dt_opt(row["heartbeat_at"]),
        budget=BudgetLimits.model_validate_json(row["budget_json"]),
        spent=SpendSnapshot.model_validate_json(row["spent_json"]),
        task_pack_sha256=row["task_pack_sha256"],
        tool_image_digest=row["tool_image_digest"] or "",
        models=json.loads(row["models_json"]),
        prompt_versions=json.loads(row["prompt_versions_json"]),
        error_reason=row["error_reason"],
        winner_id=row["winner_id"],
        git_sha=row["git_sha"],
        params=params,
        title=title,
        research_id=research_id,
    )


def _ppa_value(ppa: dict[str, Any], snake: str, camel_name: str) -> float | None:
    value = ppa.get(snake) if ppa.get(snake) is not None else ppa.get(camel_name)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _row_to_research(row: sqlite3.Row, sources: list[ResearchSourceRow]) -> ResearchRecord:
    summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
    return ResearchRecord(
        id=row["id"],
        created_at=_dt(row["created_at"]),
        expires_at=_dt(row["expires_at"]),
        key=row["key"],
        query_key=row["query_key"],
        family=row["family"],
        params=json.loads(row["params_json"]) if row["params_json"] else None,
        intent_class=row["intent_class"],
        intent_label=row["intent_label"],
        text_sha256=row["text_sha256"],
        verdict=row["verdict"],
        status=row["status"],
        queries=json.loads(row["queries_json"]),
        sentences=summary.get("sentences", []) if isinstance(summary, dict) else [],
        techniques=json.loads(row["techniques_json"]),
        done_before=bool(row["done_before"]),
        model=row["model"],
        prompt_version=row["prompt_version"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        llm_usd=row["llm_usd"],
        serper_credits=row["serper_credits"],
        tavily_credits=row["tavily_credits"],
        usd=row["usd"],
        latency_ms=row["latency_ms"],
        sources=sources,
    )


# run_outcome columns, in insert order. Column names come from this static tuple only.
_OUTCOME_COLUMNS = (
    "run_id", "research_id", "family", "params_json", "key", "priority", "mode", "status", "partial", "pack_sha256",
    "tool_image_digest", "models_json", "v1_area_um2", "v1_clock_ns", "v1_power_mw", "winner_id", "winner_label",
    "winner_strategy", "winner_technique", "winner_area_rel", "winner_clock_rel", "winner_power_rel", "winner_eqy",
    "versions", "fixes", "rejected", "usd", "llm_calls", "wall_s", "created_at", "updated_at",
)


class SqliteStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = settings.data_dir / "verity.db"
        self._files = ArtifactFiles(settings.data_dir)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ---------- lifecycle ----------
    async def open(self) -> None:
        """Create folders and tables if missing, apply pending migrations. Idempotent."""
        if self._db is not None:
            return
        await asyncio.to_thread(self._files.ensure_dirs)
        db = await aiosqlite.connect(self._db_path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await self._migrate(db)
        except BaseException:
            await db.close()
            raise
        self._db = db

    @staticmethod
    async def _migrate(db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        current = int(row[0]) if row else 0
        if current > len(MIGRATIONS):
            raise RuntimeError(
                f"database schema version {current} is newer than this build supports ({len(MIGRATIONS)})"
            )
        # UNVERIFIED: executescript with an explicit BEGIN/COMMIT and PRAGMA user_version on the installed
        # Python and aiosqlite versions (check PRAGMA user_version is 1 after the first start).
        for script in MIGRATIONS[current:]:
            await db.executescript(script)
        await db.commit()

    async def close(self) -> None:
        db, self._db = self._db, None
        if db is not None:
            await db.close()

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("store is not open: call await store.open() first")
        return self._db

    async def _write(self, sql: str, params: Sequence[Any] = ()) -> int:
        db = self._conn()
        async with self._lock:
            async with db.execute(sql, params) as cur:
                changed = cur.rowcount
            await db.commit()
            return changed

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        async with self._conn().execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        async with self._conn().execute(sql, params) as cur:
            return list(await cur.fetchall())

    # ---------- runs ----------
    async def create_run(self, run: RunRecord) -> None:
        await self._write(
            """
            INSERT INTO runs (
                id, session_id, preset_id, priority, mode, status, created_at, started_at, ended_at, heartbeat_at,
                budget_json, spent_json, task_pack_sha256, tool_image_digest, models_json, prompt_versions_json,
                error_reason, winner_id, git_sha, params_json, title, research_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.session_id,
                run.preset_id,
                run.priority,
                run.mode,
                run.status,
                _iso(run.created_at),
                _iso_opt(run.started_at),
                _iso_opt(run.ended_at),
                _iso_opt(run.heartbeat_at),
                run.budget.model_dump_json(),
                run.spent.model_dump_json(),
                run.task_pack_sha256,
                run.tool_image_digest,
                _json(run.models),
                _json(run.prompt_versions),
                run.error_reason,
                run.winner_id,
                run.git_sha,
                _json(run.params) if run.params is not None else None,
                run.title,
                run.research_id,
            ),
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        row = await self._fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        return None if row is None else _row_to_run(row)

    async def update_run(self, run_id: str, patch: RunPatch) -> None:
        """Writes only patch.model_fields_set. An explicit None on a nullable field writes NULL."""
        fields = [
            name
            for name in _PATCH_COLUMNS
            if name in patch.model_fields_set and (getattr(patch, name) is not None or name not in _NOT_NULLABLE)
        ]
        if not fields:
            return
        assignments = ", ".join(f"{_PATCH_COLUMNS[name][0]} = ?" for name in fields)
        params = [_PATCH_COLUMNS[name][1](getattr(patch, name)) for name in fields]
        changed = await self._write(f"UPDATE runs SET {assignments} WHERE id = ?", (*params, run_id))
        if changed == 0:
            raise NotFound(f"run {run_id} does not exist")

    async def touch_heartbeat(self, run_id: str, at: datetime) -> None:
        await self._write("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (_iso(at), run_id))

    async def runs_with_status(self, statuses: Sequence[RunStatus]) -> list[RunRecord]:
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = await self._fetchall(
            f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY created_at, id", tuple(statuses)
        )
        return [_row_to_run(row) for row in rows]

    async def count_runs(
        self,
        *,
        statuses: Sequence[RunStatus] | None = None,
        session_id: str | None = None,
        created_since: datetime | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses is not None:
            if not statuses:
                return 0
            clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if created_since is not None:
            clauses.append("created_at >= ?")
            params.append(_iso(created_since))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = await self._fetchone(f"SELECT COUNT(*) FROM runs{where}", params)
        return int(row[0]) if row else 0

    def _read_signoffs(self, wanted: Sequence[tuple[str, str]]) -> dict[str, RunSignoffSummary]:
        """The seeds line of each winner's sign-off sheet (final/signoff.json), by run id. Blocking: call in a thread.
        A missing or unreadable sheet, or one for another version than the winner, is left out."""
        found: dict[str, RunSignoffSummary] = {}
        for run_id, winner_id in wanted:
            try:
                raw = self._files.read_run_file(run_id, _SIGNOFF_FILE)
                sheet = Signoff.model_validate_json(raw) if raw else None
            except Exception:
                continue
            if sheet is not None and sheet.candidate_id == winner_id and sheet.tests.seeds_run > 0:
                found[run_id] = RunSignoffSummary(seeds_passed=sheet.tests.seeds_passed, seeds_run=sheet.tests.seeds_run)
        return found

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummaryItem]:
        return await self._run_summaries(limit, offset)

    async def get_run_summary(self, run_id: str) -> RunSummaryItem | None:
        rows = await self._run_summaries(1, 0, run_id)
        return rows[0] if rows else None

    async def _run_summaries(self, limit: int, offset: int, run_id: str | None = None) -> list[RunSummaryItem]:
        rows = await self._fetchall(
            """
            SELECT r.id, r.preset_id, r.params_json, r.title, r.status, r.ended_at, r.winner_id,
                   c.label AS winner_label, c.ppa_json AS winner_ppa_json,
                   o.run_id AS outcome_run_id, o.winner_area_rel, o.winner_clock_rel, o.winner_power_rel,
                   o.versions AS outcome_versions, o.usd AS outcome_usd
            FROM runs r
            LEFT JOIN candidates c ON c.run_id = r.id AND c.id = r.winner_id
            LEFT JOIN run_outcome o ON o.run_id = r.id
            WHERE (? IS NULL OR r.id = ?)
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (run_id, run_id, limit, offset),
        )
        wanted = [(row["id"], row["winner_id"]) for row in rows if row["outcome_run_id"] and row["winner_id"]]
        signoffs = await asyncio.to_thread(self._read_signoffs, wanted) if wanted else {}
        items: list[RunSummaryItem] = []
        for row in rows:
            params = json.loads(row["params_json"]) if row["params_json"] else None
            winner = None
            if row["winner_label"]:
                ppa = json.loads(row["winner_ppa_json"]) if row["winner_ppa_json"] else {}
                area = ppa.get("area_um2") if ppa.get("area_um2") is not None else ppa.get("areaUm2")
                clock = ppa.get("clock_period_ns") if ppa.get("clock_period_ns") is not None else ppa.get("clockPeriodNs")
                winner = RunWinnerSummary(label=row["winner_label"], area_um2=area, clock_period_ns=clock)
            finished_at = _iso_opt(_dt_opt(row["ended_at"]))
            title = row["title"] or row["preset_id"]
            outcome = None
            if row["outcome_run_id"]:
                outcome = RunOutcomeSummary(
                    area_vs_v1=row["winner_area_rel"],
                    clock_vs_v1=row["winner_clock_rel"],
                    power_vs_v1=row["winner_power_rel"],
                    versions=row["outcome_versions"],
                    usd=row["outcome_usd"],
                    signoff=signoffs.get(row["id"]),
                )
            items.append(
                RunSummaryItem(
                    id=row["id"],
                    preset_id=row["preset_id"],
                    params=params,
                    title=title,
                    status=row["status"],
                    finished_at=finished_at,
                    winner=winner,
                    outcome=outcome,
                )
            )
        return items

    # CPU jobs always live beside the local runs, even when accounts use Supabase.
    async def save_cpu_job(self, job: Any) -> None:
        await self._write(
            "INSERT INTO cpu_jobs (id, owner, created_at, status, record_json) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, record_json=excluded.record_json",
            (job.view.id, job.owner, job.view.created_at, job.view.status, job.model_dump_json()),
        )

    async def get_cpu_job(self, job_id: str) -> Any:
        from ..runs.cpu_job import CpuJob

        row = await self._fetchone("SELECT record_json FROM cpu_jobs WHERE id = ?", (job_id,))
        return CpuJob.model_validate_json(row[0]) if row else None

    async def list_cpu_jobs(self, owner: str | None = None, *, active: bool = False) -> list[Any]:
        from ..runs.cpu_job import CpuJob

        rows = await self._fetchall(
            "SELECT record_json FROM cpu_jobs WHERE (? IS NULL OR owner = ?) "
            "AND (? = 0 OR status IN ('running', 'integrating')) ORDER BY created_at DESC",
            (owner, owner, int(active)),
        )
        return [CpuJob.model_validate_json(row[0]) for row in rows]

    # ---------- candidates and attempts ----------
    async def upsert_candidate(self, run_id: str, candidate: Candidate) -> None:
        await self._write(
            """
            INSERT INTO candidates (
                run_id, id, label, parent_id, round, created_by, status, fitness, fitness_json, ppa_json,
                verification_json, eqy_json, reason, fix_count, is_baseline, is_elite, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, id) DO UPDATE SET
                label = excluded.label,
                parent_id = excluded.parent_id,
                round = excluded.round,
                created_by = excluded.created_by,
                status = excluded.status,
                fitness = excluded.fitness,
                fitness_json = excluded.fitness_json,
                ppa_json = excluded.ppa_json,
                verification_json = excluded.verification_json,
                eqy_json = excluded.eqy_json,
                reason = excluded.reason,
                fix_count = excluded.fix_count,
                is_baseline = excluded.is_baseline,
                is_elite = excluded.is_elite,
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                candidate.id,
                candidate.label,
                candidate.parent_id,
                candidate.round,
                candidate.created_by,
                candidate.status,
                None if candidate.fitness is None else candidate.fitness.score,
                None if candidate.fitness is None else to_json(candidate.fitness),
                None if candidate.ppa is None else to_json(candidate.ppa),
                None if candidate.verification is None else to_json(candidate.verification),
                None if candidate.eqy is None else to_json(candidate.eqy),
                candidate.reason,
                candidate.fix_count,
                int(candidate.is_baseline),
                int(candidate.is_elite),
                _now_iso(),
            ),
        )

    async def add_attempt(self, run_id: str, candidate_id: str, attempt: Attempt, meta: AttemptMeta) -> None:
        """Insert keyed by (run_id, candidate_id, n). Re-adding the same n replaces the verification and the
        attempt fields; meta fields that are unset in the new call keep their stored value."""
        await self._write(
            """
            INSERT INTO attempts (
                run_id, candidate_id, n, attempt_by, reason, rtl_artifact, rtl_sha256, verification_json, model_id,
                prompt_version, llm_request_id, input_tokens, output_tokens, tool_image_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, candidate_id, n) DO UPDATE SET
                attempt_by = excluded.attempt_by,
                reason = excluded.reason,
                verification_json = excluded.verification_json,
                rtl_artifact = COALESCE(excluded.rtl_artifact, attempts.rtl_artifact),
                rtl_sha256 = COALESCE(excluded.rtl_sha256, attempts.rtl_sha256),
                model_id = COALESCE(excluded.model_id, attempts.model_id),
                prompt_version = COALESCE(excluded.prompt_version, attempts.prompt_version),
                llm_request_id = COALESCE(excluded.llm_request_id, attempts.llm_request_id),
                input_tokens = CASE WHEN excluded.input_tokens > 0 THEN excluded.input_tokens
                                    ELSE attempts.input_tokens END,
                output_tokens = CASE WHEN excluded.output_tokens > 0 THEN excluded.output_tokens
                                     ELSE attempts.output_tokens END,
                tool_image_digest = COALESCE(excluded.tool_image_digest, attempts.tool_image_digest)
            """,
            (
                run_id,
                candidate_id,
                attempt.n,
                attempt.by,
                attempt.reason,
                meta.rtl_artifact,
                meta.rtl_sha256 or (sha256_hex(attempt.rtl.encode("utf-8")) if attempt.rtl else None),
                to_json(attempt.verification),
                meta.model_id,
                meta.prompt_version,
                meta.llm_request_id,
                meta.input_tokens,
                meta.output_tokens,
                meta.tool_image_digest,
                _iso(meta.created_at),
            ),
        )

    async def candidates_for_run(self, run_id: str) -> list[CandidateRow]:
        rows = await self._fetchall(
            """
            SELECT id, label, round, parent_id, created_by, status, fitness, ppa_json, eqy_json, fix_count, is_baseline
            FROM candidates WHERE run_id = ? ORDER BY round, id
            """,
            (run_id,),
        )
        out: list[CandidateRow] = []
        for row in rows:
            ppa = json.loads(row["ppa_json"]) if row["ppa_json"] else {}
            eqy = json.loads(row["eqy_json"]) if row["eqy_json"] else {}
            out.append(
                CandidateRow(
                    id=row["id"],
                    label=row["label"],
                    round=row["round"],
                    parent_id=row["parent_id"],
                    created_by=row["created_by"],
                    status=row["status"],
                    fitness=row["fitness"],
                    area_um2=_ppa_value(ppa, "area_um2", "areaUm2"),
                    clock_period_ns=_ppa_value(ppa, "clock_period_ns", "clockPeriodNs"),
                    power_mw=_ppa_value(ppa, "power_mw", "powerMw"),
                    eqy=eqy.get("status") if isinstance(eqy, dict) else None,
                    fix_count=row["fix_count"],
                    is_baseline=bool(row["is_baseline"]),
                )
            )
        return out

    # ---------- events ----------
    async def append_event(self, run_id: str, event: EventBase) -> None:
        """event is already stamped. Raises DuplicateEvent if (run_id, seq) exists."""
        try:
            await self._write(
                "INSERT INTO events (run_id, seq, t_ms, json) VALUES (?, ?, ?, ?)",
                (run_id, event.seq, event.t_ms, event_to_json(event)),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEvent(f"event seq {event.seq} already exists for run {run_id}") from exc

    async def events_after(self, run_id: str, after_seq: int = 0) -> list[EventBase]:
        rows = await self._fetchall(
            "SELECT json FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        )
        return [parse_event_json(row["json"]) for row in rows]

    async def last_seq(self, run_id: str) -> int:
        row = await self._fetchone("SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,))
        return int(row[0]) if row else 0

    # ---------- artifacts (write-once) ----------
    async def put_blob(self, data: bytes, mime: str) -> ArtifactRef:
        sha, path = await asyncio.to_thread(self._files.put_blob, data)
        await self._write(
            "INSERT OR IGNORE INTO artifacts (sha256, path, bytes, mime, created_at) VALUES (?, ?, ?, ?, ?)",
            (sha, str(path), len(data), mime, _now_iso()),
        )
        return ArtifactRef(sha256=sha, path=str(path), size_bytes=len(data), mime=mime)

    async def write_run_file(self, run_id: str, relpath: str, data: bytes, mime: str, *, overwrite: bool = False) -> ArtifactRef:
        sha, path = await asyncio.to_thread(self._files.write_run_file, run_id, relpath, data, overwrite=overwrite)
        await self._write(
            "INSERT OR IGNORE INTO artifacts (sha256, path, bytes, mime, created_at) VALUES (?, ?, ?, ?, ?)",
            (sha, str(self._files.blob_path(sha)), len(data), mime, _now_iso()),
        )
        return ArtifactRef(sha256=sha, path=str(path), size_bytes=len(data), mime=mime)

    async def read_run_file(self, run_id: str, relpath: str) -> bytes | None:
        return await asyncio.to_thread(self._files.read_run_file, run_id, relpath)

    # ---------- evals and spend ----------
    async def add_eval_result(self, row: EvalResultRow) -> None:
        await self._write(
            """
            INSERT INTO eval_results (eval_run_id, task_id, suite, metrics_json, git_sha, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row.eval_run_id, row.task_id, row.suite, _json(row.metrics), row.git_sha, _iso(row.created_at)),
        )

    async def add_daily_spend(self, day: str, usd: float, session_id: str | None = None) -> None:
        """day = UTC 'YYYY-MM-DD'. Adds (not sets). With a session id, the same amount is added to that owner's day
        (session_spend, migration 5) in the same transaction, so the two totals cannot drift."""
        if not _DAY_RE.fullmatch(day):
            raise ValueError(f"invalid day {day!r}, expected YYYY-MM-DD")
        db = self._conn()
        async with self._lock:
            try:
                await db.execute(
                    """
                    INSERT INTO daily_spend (day, usd) VALUES (?, ?)
                    ON CONFLICT (day) DO UPDATE SET usd = daily_spend.usd + excluded.usd
                    """,
                    (day, usd),
                )
                if session_id is not None:
                    await db.execute(
                        """
                        INSERT INTO session_spend (session_id, day, usd) VALUES (?, ?, ?)
                        ON CONFLICT (session_id, day) DO UPDATE SET usd = session_spend.usd + excluded.usd
                        """,
                        (spend_owner(session_id), day, usd),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def daily_spend(self, day: str) -> float:
        row = await self._fetchone("SELECT usd FROM daily_spend WHERE day = ?", (day,))
        return float(row[0]) if row else 0.0

    async def session_spend(self, session_id: str, day: str) -> float:
        row = await self._fetchone(
            "SELECT usd FROM session_spend WHERE session_id = ? AND day = ?", (spend_owner(session_id), day)
        )
        return float(row[0]) if row else 0.0

    async def add_serper_credits(self, day: str, credits: int) -> None:
        """Daily Serper credit counter, next to the daily dollars. Adds (not sets)."""
        if not _DAY_RE.fullmatch(day):
            raise ValueError(f"invalid day {day!r}, expected YYYY-MM-DD")
        await self._write(
            """
            INSERT INTO daily_spend (day, usd, serper_credits) VALUES (?, 0, ?)
            ON CONFLICT (day) DO UPDATE SET serper_credits = daily_spend.serper_credits + excluded.serper_credits
            """,
            (day, int(credits)),
        )

    async def serper_credits(self, day: str) -> int:
        row = await self._fetchone("SELECT serper_credits FROM daily_spend WHERE day = ?", (day,))
        return int(row[0]) if row and row[0] is not None else 0

    async def add_tavily_credits(self, day: str, credits: int) -> None:
        """Daily Tavily credit counter (migration 4), next to the daily dollars. Adds (not sets)."""
        if not _DAY_RE.fullmatch(day):
            raise ValueError(f"invalid day {day!r}, expected YYYY-MM-DD")
        await self._write(
            """
            INSERT INTO daily_spend (day, usd, tavily_credits) VALUES (?, 0, ?)
            ON CONFLICT (day) DO UPDATE SET tavily_credits = daily_spend.tavily_credits + excluded.tavily_credits
            """,
            (day, int(credits)),
        )

    async def tavily_credits(self, day: str) -> int:
        row = await self._fetchone("SELECT tavily_credits FROM daily_spend WHERE day = ?", (day,))
        return int(row[0]) if row and row[0] is not None else 0

    # ---------- research (migration 3) ----------
    async def put_research(self, record: ResearchRecord) -> None:
        """Insert one research record with its sources in one transaction. Sources are shared by canonical URL."""
        db = self._conn()
        now = _now_iso()
        async with self._lock:
            try:
                await db.execute(
                    """
                    INSERT INTO research (
                        id, created_at, expires_at, key, query_key, family, params_json, intent_class, intent_label,
                        text_sha256, verdict, status, queries_json, summary_json, techniques_json, done_before, model,
                        prompt_version, input_tokens, output_tokens, llm_usd, serper_credits, tavily_credits, usd,
                        latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        _iso(record.created_at),
                        _iso(record.expires_at),
                        record.key,
                        record.query_key,
                        record.family,
                        _json(record.params) if record.params is not None else None,
                        record.intent_class,
                        record.intent_label,
                        record.text_sha256,
                        record.verdict,
                        record.status,
                        _json(record.queries),
                        _json({"sentences": record.sentences}),
                        _json(record.techniques),
                        int(record.done_before),
                        record.model,
                        record.prompt_version,
                        record.input_tokens,
                        record.output_tokens,
                        record.llm_usd,
                        record.serper_credits,
                        record.tavily_credits,
                        record.usd,
                        record.latency_ms,
                    ),
                )
                for src in record.sources:
                    source_id = sha256_hex(src.url.encode("utf-8"))
                    await db.execute(
                        """
                        INSERT INTO source (id, url, domain, tier, title, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (id) DO UPDATE SET last_seen = excluded.last_seen, title = excluded.title
                        """,
                        (source_id, src.url, src.domain, src.tier, src.title, now, now),
                    )
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO research_source (
                            research_id, source_id, n, endpoint, query_idx, rank, snippet, pub_date_raw, score,
                            relevance, cited
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            source_id,
                            src.n,
                            src.endpoint,
                            src.query_idx,
                            src.rank,
                            src.snippet,
                            src.date,
                            src.score,
                            src.relevance,
                            int(src.cited),
                        ),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _research_sources(self, research_id: str) -> list[ResearchSourceRow]:
        rows = await self._fetchall(
            """
            SELECT rs.n, s.url, s.domain, s.tier, s.title, rs.endpoint, rs.query_idx, rs.rank, rs.snippet,
                   rs.pub_date_raw, rs.score, rs.relevance, rs.cited
            FROM research_source rs JOIN source s ON s.id = rs.source_id
            WHERE rs.research_id = ? ORDER BY rs.n
            """,
            (research_id,),
        )
        return [
            ResearchSourceRow(
                n=row["n"],
                url=row["url"],
                domain=row["domain"],
                tier=row["tier"],
                title=row["title"],
                endpoint=row["endpoint"],
                query_idx=row["query_idx"],
                rank=row["rank"],
                snippet=row["snippet"],
                date=row["pub_date_raw"],
                score=row["score"],
                relevance=row["relevance"],
                cited=bool(row["cited"]),
            )
            for row in rows
        ]

    async def get_research(self, research_id: str) -> ResearchRecord | None:
        row = await self._fetchone("SELECT * FROM research WHERE id = ?", (research_id,))
        if row is None:
            return None
        return _row_to_research(row, await self._research_sources(research_id))

    async def _fresh_research(self, column: str, value: str, now: datetime) -> ResearchRecord | None:
        # `column` is one of the two literals passed by the methods below, never input.
        row = await self._fetchone(
            f"""
            SELECT * FROM research WHERE {column} = ? AND status = 'ok' AND expires_at > ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (value, _iso(now)),
        )
        if row is None:
            return None
        return _row_to_research(row, await self._research_sources(row["id"]))

    async def research_by_key(self, key: str, now: datetime) -> ResearchRecord | None:
        """The newest fresh (not expired) research with status ok for this family-plus-knobs key."""
        return await self._fresh_research("key", key, now)

    async def research_by_query_key(self, query_key: str, now: datetime) -> ResearchRecord | None:
        """The newest fresh research with status ok for these templated queries."""
        return await self._fresh_research("query_key", query_key, now)

    async def research_for_family(self, family: str, now: datetime, limit: int = 50) -> list[ResearchRecord]:
        """Fresh ok in-family research of one family, newest first, without sources (nearest-knob lookup)."""
        rows = await self._fetchall(
            """
            SELECT * FROM research
            WHERE family = ? AND key IS NOT NULL AND status = 'ok' AND expires_at > ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (family, _iso(now), limit),
        )
        return [_row_to_research(row, []) for row in rows]

    async def grant_research(self, research_id: str, session_hmac: str) -> None:
        await self._write(
            "INSERT OR IGNORE INTO research_grant (research_id, session_hmac, created_at) VALUES (?, ?, ?)",
            (research_id, session_hmac, _now_iso()),
        )

    async def research_granted(self, research_id: str, session_hmac: str) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM research_grant WHERE research_id = ? AND session_hmac = ?", (research_id, session_hmac)
        )
        return row is not None

    # ---------- run outcomes (migration 3) ----------
    async def put_run_outcome(self, row: RunOutcomeRow) -> None:
        """Idempotent upsert keyed by run_id: the orchestrator writes it at its ending, the run manager again at
        finalize (stop, error and crash included). created_at keeps the first write."""
        values: dict[str, Any] = row.model_dump()
        values["params_json"] = _json(values.pop("params"))
        values["models_json"] = _json(values.pop("models"))
        values["partial"] = int(row.partial)
        values["created_at"] = _iso(row.created_at)
        values["updated_at"] = _now_iso()
        marks = ", ".join("?" for _ in _OUTCOME_COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _OUTCOME_COLUMNS if c not in ("run_id", "created_at"))
        await self._write(
            f"INSERT INTO run_outcome ({', '.join(_OUTCOME_COLUMNS)}) VALUES ({marks}) "
            f"ON CONFLICT (run_id) DO UPDATE SET {updates}",
            tuple(values[c] for c in _OUTCOME_COLUMNS),
        )

    async def put_strategy_outcomes(self, run_id: str, rows: Sequence[StrategyOutcomeRow]) -> None:
        """Replace this run's strategy rows (one per version tried after v1) in one transaction."""
        db = self._conn()
        async with self._lock:
            try:
                await db.execute("DELETE FROM strategy_outcome WHERE run_id = ?", (run_id,))
                for r in rows:
                    await db.execute(
                        """
                        INSERT INTO strategy_outcome (
                            run_id, candidate_id, label, key, pack_sha256, tool_image_digest, strategy, technique,
                            result, area_rel, clock_rel, power_rel
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            r.candidate_id,
                            r.label,
                            r.key,
                            r.pack_sha256,
                            r.tool_image_digest,
                            r.strategy,
                            r.technique,
                            r.result,
                            r.area_rel,
                            r.clock_rel,
                            r.power_rel,
                        ),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    @staticmethod
    def _row_to_outcome(row: sqlite3.Row) -> RunOutcomeRow:
        data: dict[str, Any] = {c: row[c] for c in _OUTCOME_COLUMNS if c not in ("params_json", "models_json", "updated_at")}
        data["params"] = json.loads(row["params_json"]) if row["params_json"] else {}
        data["models"] = json.loads(row["models_json"]) if row["models_json"] else {}
        data["partial"] = bool(row["partial"])
        data["created_at"] = _dt(row["created_at"])
        return RunOutcomeRow(**data)

    async def get_run_outcome(self, run_id: str) -> RunOutcomeRow | None:
        row = await self._fetchone("SELECT * FROM run_outcome WHERE run_id = ?", (run_id,))
        return None if row is None else self._row_to_outcome(row)

    async def run_outcomes(self, key: str, pack_sha256: str, tool_image_digest: str, limit: int = 20) -> list[RunOutcomeRow]:
        """Comparable earlier runs only: same family-plus-knobs key, same task pack hash and same tool image."""
        rows = await self._fetchall(
            """
            SELECT * FROM run_outcome WHERE key = ? AND pack_sha256 = ? AND tool_image_digest = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (key, pack_sha256, tool_image_digest, limit),
        )
        return [self._row_to_outcome(row) for row in rows]

    async def strategy_outcomes(
        self, key: str, pack_sha256: str, tool_image_digest: str, limit: int = 200
    ) -> list[StrategyOutcomeRow]:
        rows = await self._fetchall(
            """
            SELECT * FROM strategy_outcome WHERE key = ? AND pack_sha256 = ? AND tool_image_digest = ?
            ORDER BY run_id, candidate_id LIMIT ?
            """,
            (key, pack_sha256, tool_image_digest, limit),
        )
        return [StrategyOutcomeRow.model_validate({k: row[k] for k in row.keys()}) for row in rows]

    # ---------- threads (migration 6; core.protocols.ThreadStore) ----------
    @staticmethod
    def _facts(raw: str) -> dict[str, Any]:
        try:
            facts = json.loads(raw)
        except ValueError:
            return {}
        return facts if isinstance(facts, dict) else {}

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> ThreadTurnRow:
        try:
            steps = json.loads(row["steps_json"]) if row["steps_json"] else []
        except ValueError:
            steps = []
        try:
            reply = json.loads(row["reply_json"]) if row["reply_json"] else None
        except ValueError:
            reply = None
        return ThreadTurnRow(
            seq=int(row["seq"]),
            role=row["role"],
            text=row["text"],
            steps=[s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else [],
            reply=reply if isinstance(reply, dict) else None,
            created_at=_dt(row["created_at"]),
        )

    async def load(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData | None:
        row = await self._fetchone("SELECT * FROM threads WHERE owner = ? AND id = ?", (owner, thread_id))
        if row is None:
            return None
        through = int(row["summarized_through"])
        rows = await self._fetchall(
            "SELECT * FROM thread_turns WHERE owner = ? AND thread_id = ? AND seq > ? ORDER BY seq",
            (owner, thread_id, through if since_summary else 0),
        )
        return ThreadData(
            owner=row["owner"],
            id=row["id"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            facts=self._facts(row["facts_json"]),
            summary=row["summary"],
            summarized_through=through,
            turns=[self._row_to_turn(r) for r in rows],
        )

    async def get_or_create(self, owner: str, thread_id: str, *, since_summary: bool = False) -> ThreadData:
        now = _now_iso()
        await self._write(
            "INSERT OR IGNORE INTO threads (owner, id, created_at, updated_at, facts_json) VALUES (?, ?, ?, ?, '{}')",
            (owner, thread_id, now, now),
        )
        data = await self.load(owner, thread_id, since_summary=since_summary)
        assert data is not None  # the row was just inserted, or was there
        return data

    async def update_facts(self, owner: str, thread_id: str, facts: dict[str, Any]) -> None:
        changed = await self._write(
            "UPDATE threads SET facts_json = ?, updated_at = ? WHERE owner = ? AND id = ?",
            (_json(facts), _now_iso(), owner, thread_id),
        )
        if not changed:
            raise NotFound(thread_id)

    async def update_summary(self, owner: str, thread_id: str, summary: str, through_seq: int) -> None:
        changed = await self._write(
            "UPDATE threads SET summary = ?, summarized_through = ?, updated_at = ? WHERE owner = ? AND id = ?",
            (summary, through_seq, _now_iso(), owner, thread_id),
        )
        if not changed:
            raise NotFound(thread_id)

    async def append_turns(self, owner: str, thread_id: str, turns: Sequence[ThreadTurnIn]) -> int:
        """Add the rows in one transaction, cut to THREAD_USER_MAX and THREAD_VERITY_MAX characters. Returns the seq of
        the last row (the current one when `turns` is empty). Raises NotFound for a thread that does not exist."""
        db = self._conn()
        now = _now_iso()
        async with self._lock:
            try:
                async with db.execute("SELECT 1 FROM threads WHERE owner = ? AND id = ?", (owner, thread_id)) as cur:
                    if await cur.fetchone() is None:
                        raise NotFound(thread_id)
                async with db.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_turns WHERE owner = ? AND thread_id = ?", (owner, thread_id)
                ) as cur:
                    seq = int((await cur.fetchone())[0])  # type: ignore[index]
                for turn in turns:
                    seq += 1
                    cap = THREAD_USER_MAX if turn.role == "user" else THREAD_VERITY_MAX
                    reply = capped_reply(turn.reply) if turn.role == "verity" else None
                    await db.execute(
                        "INSERT INTO thread_turns (owner, thread_id, seq, role, text, steps_json, reply_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            owner, thread_id, seq, turn.role, turn.text[:cap],
                            _json(turn.steps) if turn.steps else None, _json(reply) if reply else None, now,
                        ),
                    )
                await db.execute("UPDATE threads SET updated_at = ? WHERE owner = ? AND id = ?", (now, owner, thread_id))
                await db.commit()
                return seq
            except BaseException:
                await db.rollback()
                raise

    async def list_threads(self, owner: str, limit: int = 50) -> list[ThreadListItem]:
        """This owner's threads that have at least one message, newest first."""
        rows = await self._fetchall(
            """
            SELECT t.id, t.updated_at, t.facts_json,
                   (SELECT text FROM thread_turns n WHERE n.owner = t.owner AND n.thread_id = t.id AND n.role = 'user'
                    ORDER BY n.seq LIMIT 1) AS first_text
            FROM threads t WHERE t.owner = ? AND first_text IS NOT NULL
            ORDER BY t.updated_at DESC, t.id LIMIT ?
            """,
            (owner, max(0, limit)),
        )
        out = []
        for row in rows:
            run_ids = self._facts(row["facts_json"]).get("run_ids", [])
            out.append(
                ThreadListItem(
                    id=row["id"],
                    title=thread_title(row["first_text"]),
                    updated_at=_dt(row["updated_at"]),
                    run_ids=[r for r in run_ids if isinstance(r, str)] if isinstance(run_ids, list) else [],
                )
            )
        return out

    async def claim(self, from_owner: str, to_owner: str) -> int:
        """Move every thread (and its turns) of one owner to another, for the day a signed-in user claims the threads
        of the session that came before. Returns how many threads moved. A thread id the target already has stays where
        it is."""
        if from_owner == to_owner:
            return 0
        db = self._conn()
        async with self._lock:
            try:
                async with db.execute("UPDATE OR IGNORE threads SET owner = ? WHERE owner = ?", (to_owner, from_owner)) as cur:
                    moved = cur.rowcount
                await db.execute("UPDATE OR IGNORE thread_turns SET owner = ? WHERE owner = ?", (to_owner, from_owner))
                await db.commit()
                return moved
            except BaseException:
                await db.rollback()
                raise

    # ---------- run index (migration 7; core.protocols.ThreadStore) ----------
    async def add_user_run(self, owner: str, run_id: str, thread_id: str | None, title: str) -> None:
        await self._write(
            "INSERT OR IGNORE INTO user_runs (owner, run_id, thread_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
            (owner, run_id, thread_id, title, _now_iso()),
        )

    async def list_user_runs(self, owner: str, limit: int = 50) -> list[UserRunRow]:
        rows = await self._fetchall(
            "SELECT * FROM user_runs WHERE owner = ? ORDER BY created_at DESC, run_id LIMIT ?", (owner, max(0, limit))
        )
        return [
            UserRunRow(run_id=r["run_id"], title=r["title"], thread_id=r["thread_id"], created_at=_dt(r["created_at"]))
            for r in rows
        ]

    # ---------- user credits (migration 8; core.protocols.ThreadStore) ----------
    async def get_credits(self, owner: str, default: int = 3) -> int:
        row = await self._fetchone("SELECT credits FROM user_credits WHERE owner = ?", (owner,))
        if row is not None:
            return int(row["credits"])
        now = _now_iso()
        await self._write(
            "INSERT OR IGNORE INTO user_credits (owner, credits, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (owner, default, now, now),
        )
        row = await self._fetchone("SELECT credits FROM user_credits WHERE owner = ?", (owner,))
        return int(row["credits"]) if row is not None else default

    async def deduct_credit(self, owner: str, default: int = 3) -> int:
        now = _now_iso()
        db = self._conn()
        async with self._lock:
            try:
                async with db.execute("SELECT credits FROM user_credits WHERE owner = ?", (owner,)) as cur:
                    row = await cur.fetchone()
                if row is None:
                    await db.execute(
                        "INSERT INTO user_credits (owner, credits, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (owner, default, now, now),
                    )
                    credits = default
                else:
                    credits = int(row[0])
                if credits <= 0:
                    await db.commit()
                    return -1
                new_credits = credits - 1
                await db.execute(
                    "UPDATE user_credits SET credits = ?, updated_at = ? WHERE owner = ?",
                    (new_credits, now, owner),
                )
                await db.commit()
                return new_credits
            except BaseException:
                await db.rollback()
                raise

    async def set_credits(
        self, owner: str, credits: int, email: str | None = None, role: str | None = None
    ) -> None:
        now = _now_iso()
        db = self._conn()
        async with self._lock:
            try:
                async with db.execute("SELECT owner, email, role FROM user_credits WHERE owner = ?", (owner,)) as cur:
                    row = await cur.fetchone()
                if row is None:
                    await db.execute(
                        """
                        INSERT INTO user_credits (owner, credits, email, role, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (owner, credits, email, role or "user", now, now),
                    )
                else:
                    new_email = email if email is not None else row[1]
                    new_role = role if role is not None else (row[2] or "user")
                    await db.execute(
                        """
                        UPDATE user_credits
                        SET credits = ?, email = ?, role = ?, updated_at = ?
                        WHERE owner = ?
                        """,
                        (credits, new_email, new_role, now, owner),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def list_user_credits(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT owner, credits, email, role, created_at, updated_at
            FROM user_credits
            ORDER BY created_at DESC
            """
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            owner = r["owner"]
            run_count_row = await self._fetchone(
                """
                SELECT COUNT(DISTINCT run_id) AS cnt FROM (
                    SELECT run_id FROM user_runs WHERE owner = ?
                    UNION
                    SELECT id AS run_id FROM runs WHERE session_id = ?
                )
                """,
                (owner, owner),
            )
            runs_count = int(run_count_row["cnt"]) if run_count_row is not None else 0

            spend_row = await self._fetchone(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total_spend FROM run_outcome WHERE run_id IN (
                    SELECT run_id FROM user_runs WHERE owner = ?
                    UNION
                    SELECT id AS run_id FROM runs WHERE session_id = ?
                )
                """,
                (owner, owner),
            )
            total_spent_usd = float(spend_row["total_spend"]) if spend_row is not None else 0.0

            out.append(
                {
                    "owner": owner,
                    "credits": int(r["credits"]),
                    "email": r["email"],
                    "role": r["role"] or "user",
                    "runs_count": runs_count,
                    "total_spent_usd": round(total_spent_usd, 4),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return out

