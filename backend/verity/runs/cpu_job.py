"""CPU Lab orchestration. Every result and dollar comes from a normal run record."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..config import Settings
from ..contracts.cpu import (
    CpuBlockView, CpuCoreView, CpuEvidence, CpuJobView, CpuTotals, CpuVersionView, CreateCpuJobRequest,
    UpgradeRequest,
)
from ..contracts.cpu_profile import CpuProfile
from ..contracts.signoff import Signoff
from ..core.budget import spend_owner
from ..core.errors import AdmissionDenied, Conflict, Forbidden, NotFound, RtlRejected, UnknownPreset
from ..core.pack import MANIFEST_NAME, TaskPack, manifest_entries, write_manifest
from ..core.records import ACTIVE_STATUSES, RunRecord
from ..core.rtl import Rtl
from ..cpu.compare import compare_profiles
from ..cpu.upgrades import CATALOG
from ..research.cpu import cpu_block_research
from ..roles.rtl_gen import ascii_line
from ..tools import rvfi as _rvfi_install  # noqa: F401  (registers ToolRunner.rvfi on the sandbox runner)

if TYPE_CHECKING:
    from .manager import RunManager

log = logging.getLogger("verity.runs.cpu_job")

# Contract order is also the order of modules in the frozen blocks.v.
BLOCKS = (
    ("rv32i_imm", "Immediate generator"),
    ("rv32i_dec", "Instruction decoder"),
    ("rv32i_alu", "Arithmetic logic unit"),
    ("rv32i_br", "Branch comparator"),
    ("rv32i_lsu", "Load/store unit"),
    ("rv32i_rf", "Register file"),
    ("rv32i_ctl", "Control FSM"),
)


FLASH_WAIT_CYCLES = 3  # the "flash-like memory" profile
# Longest list of failing riscv-formal check names put in a message.
RVFI_NAMES_SHOWN = 8


@dataclass(frozen=True, slots=True)
class UpgradeJob:
    """How one catalog upgrade becomes a child job."""

    spec_id: str
    core_pack: str  # the static core pack the child derives
    new_block: str  # the one block pack the child runs
    reused: tuple[str, ...]  # block packs taken from the parent's winners, in blocks.v order
    flash: bool  # also bench with flash-like memory
    rvfi_gate: bool  # a riscv-formal failure fails the job
    parent_core: bool  # blocks.v also holds the parent's core winner, before the new block


UPGRADE_JOBS = {
    "pipeline_2": UpgradeJob(
        "rv32i_pipe2", "rv32i_pipe2_core", "rv32i_pctl",
        ("rv32i_imm", "rv32i_dec", "rv32i_alu", "rv32i_br", "rv32i_lsu", "rv32i_rf"),
        flash=False, rvfi_gate=True, parent_core=False,
    ),
    "cache_16": UpgradeJob(
        "rv32i_cached", "rv32i_cache_core", "rv32i_cache", tuple(p for p, _ in BLOCKS),
        flash=True, rvfi_gate=False, parent_core=True,
    ),
}


ACTIVE_JOB_STATUSES = ("running", "integrating", "improving")
# Any signed-in visitor builds; this is the 403 reason for the one who is not signed in.
SIGN_IN_TO_BUILD = "Sign in to build a CPU."
# One run's own cap (config [budget.run] max_usd is 0.50). An improve round needs the block run and a core rerun.
RUN_CAP_USD = 0.50
# Docker can stall for a while (a laptop waking from standby). A block run refused only because the sandbox did not
# answer is retried this many times, this many seconds apart, before the job fails.
SANDBOX_RETRIES = 6
SANDBOX_RETRY_S = 30.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


# core_failure_notes: what one ended core run's own files say went wrong. Bounded: at most this many lines, characters
# per line, attempt files read, and bytes of one file (a bigger file is skipped).
NOTE_LINES = 8
NOTE_CHARS = 160
NOTE_ATTEMPTS = 20
NOTE_FILE_BYTES = 256_000
NOTE_RUNLOG_BYTES = 4_000_000
NOTE_CASES = 2  # failing simulation cases named
NOTE_LINT_LINES = 3  # lint error lines named
PRIOR_LINES = 12  # most lines a core retry's first RTL generation is told about the failed core
DIAGNOSIS_PREFIX = "Diagnosis: "  # captions.diagnosis
# Diagnosis captions that code wrote, not the model (the declaration fixer, a restart, a repair answer with none).
CODE_DIAGNOSES = (
    "Declaration fix without a model call", "The same failure came back twice", "The reported failure was addressed",
)
def _verification(raw: bytes) -> Any:
    """An attempt's verify file as a wire Verification: the sandbox's VerifyResult, or the Verification written when the
    RTL rules rejected the design before the sandbox. None when it is neither."""
    from ..contracts import Verification
    from ..core.results import VerifyResult

    try:
        result = VerifyResult.model_validate_json(raw)
        return result.to_verification(result.sim.total if result.sim is not None else 0)
    except ValueError:
        pass
    try:
        return Verification.model_validate_json(raw)
    except ValueError:
        return None


def _lint_errors(log: str | None) -> list[str]:
    """The first error lines of a lint log (Verilator, the interface check or the RTL rules), else its first lines."""
    rows = [ln.strip() for ln in (log or "").splitlines() if ln.strip()]
    errors = [
        ln.removeprefix("- ") for ln in rows
        if (ln.startswith(("%Error", "- ")) or "error" in ln.lower()) and not ln.startswith("%Error: Exiting")
    ]
    return (errors or rows)[:NOTE_LINT_LINES]


async def _model_diagnosis(store: Any, run_id: str) -> str | None:
    """The newest diagnosis caption of a run that the model wrote, from its runlog.json. None when there is none."""
    raw = await store.read_run_file(run_id, "runlog.json")
    if not raw or len(raw) > NOTE_RUNLOG_BYTES:
        return None
    try:
        events = json.loads(raw).get("events", [])
    except (ValueError, AttributeError):
        return None
    said = None
    for event in events if isinstance(events, list) else []:
        text = event.get("text") if isinstance(event, dict) and event.get("t") == "caption" else None
        if isinstance(text, str) and text.startswith(DIAGNOSIS_PREFIX):
            body = text.removeprefix(DIAGNOSIS_PREFIX).strip()
            if body and not body.startswith(CODE_DIAGNOSES):
                said = body
    return said


async def core_failure_notes(store: Any, run_id: str, *, diagnosis: bool = True) -> list[str]:
    """At most NOTE_LINES short lines on one ended core run, read by code from its own files: the tests passed of the
    total, the stage that failed (lint or simulation), the first lint or simulation error lines (sandbox output) and,
    unless `diagnosis` is False, the model's own diagnosis caption. A file that is missing, too big or unreadable just
    drops its lines. The lines are the run's own data: put each through memory.data() before it goes into chat facts."""
    checks = []
    for n in range(1, NOTE_ATTEMPTS + 1):
        raw = await store.read_run_file(run_id, f"candidates/c1/attempt-{n}.verify.json")
        if raw is None:
            break
        checked = _verification(raw) if len(raw) <= NOTE_FILE_BYTES else None
        if checked is not None:
            checks.append(checked)
    lines: list[str] = []
    if checks:
        last = checks[-1]
        sim = next((v for v in reversed(checks) if v.lint == "pass" and v.total), None)
        if sim is not None:
            lines.append(f"tests passed: {sim.passed} of {sim.total}")
        else:
            lines.append("tests passed: none, no attempt reached simulation")
        if last.lint == "fail":
            lines.append("stage that failed: lint")
        elif last.passed < last.total or not last.total:  # no total: the simulation did not run to its end
            lines.append("stage that failed: simulation")
        cases = sim if sim is not None else (last if last.lint == "pass" else None)
        if cases is not None:
            for case in [c for c in cases.failures if not c.pass_][:NOTE_CASES]:
                lines.append(f"failing test {case.name}: expected {case.expected}, got {case.got}")
        if last.lint == "fail":
            lines += [f"lint: {err}" for err in _lint_errors(last.lint_log)]
    if diagnosis:
        said = await _model_diagnosis(store, run_id)
        if said:
            lines.append(f"model diagnosis: {said}")
    return [ln for ln in (ascii_line(raw_line, NOTE_CHARS) for raw_line in lines) if ln][:NOTE_LINES]


def _with_thread(job: "CpuJob", session_id: str, retries: Mapping[str, str] | None = None) -> CpuJobView:
    """The job's view with its chat id and its newest core retry, for the owner only (an admin reading another account's
    job cannot open that chat). `retries` maps a job id to its newest retry among the owner's jobs (_newest_retries)."""
    mine = job.owner == spend_owner(session_id)
    return job.view.model_copy(update={
        "thread_id": job.thread_id if mine else None,
        "retry_job_id": (retries or {}).get(job.view.id) if mine else None,
    })


def _newest_retries(jobs: Sequence["CpuJob"]) -> dict[str, str]:
    """Job id -> the id of the newest core retry started from it, among `jobs` (one owner's, newest first as the store
    lists them)."""
    out: dict[str, str] = {}
    for job in jobs:
        if job.view.retry_of and job.view.retry_of not in out:
            out[job.view.retry_of] = job.view.id
    return out


@dataclass(frozen=True, slots=True)
class Measure:
    """One block winner's measured numbers, read from its run record. None where the run did not measure it."""

    run_id: str | None = None
    area_um2: float | None = None
    clock_ns: float | None = None
    power_mw: float | None = None


_GOAL_FIELD = {"area": "area_um2", "speed": "clock_ns", "power": "power_mw"}


def goal_value(goal: str, measure: Measure) -> float | None:
    return getattr(measure, _GOAL_FIELD[goal])


def pick_block(goal: str, wins: Mapping[str, Measure]) -> str | None:
    """The block with the largest cost on the goal (area: um2, speed: longest clock period, power: mW).
    Ties go to the earlier block in contract order. None when no block reported the goal's number."""
    best: tuple[float, str] | None = None
    for block_id, _ in BLOCKS:
        value = goal_value(goal, wins[block_id]) if block_id in wins else None
        if value is not None and (best is None or value > best[0]):
            best = (value, block_id)
    return best[1] if best else None


def is_better(goal: str, new: Measure, old: Measure) -> bool:
    """Strictly lower on the goal. An unmeasured number never counts as better."""
    a, b = goal_value(goal, new), goal_value(goal, old)
    return a is not None and b is not None and a < b


def version_totals(wins: Mapping[str, Measure], block_ids: Sequence[str] | None = None) -> CpuTotals:
    """Area and power are sums over the block winners, the clock is the longest block clock period. A part is
    None when any of the blocks (the seven by default) has no value for it."""
    rows = [wins.get(block_id, Measure()) for block_id in block_ids or [p for p, _ in BLOCKS]]

    def part(name: str, combine: Any) -> float | None:
        values = [getattr(r, name) for r in rows]
        return None if any(v is None for v in values) else round(combine(values), 6)

    return CpuTotals(area_um2=part("area_um2", sum), clock_ns=part("clock_ns", max), power_mw=part("power_mw", sum))


def area_by_block(wins: Mapping[str, Measure], block_ids: Sequence[str]) -> dict[str, float]:
    """Each block winner's measured area, or nothing when any block has none: a partial sum would understate."""
    areas = {b: wins[b].area_um2 for b in block_ids if b in wins and wins[b].area_um2 is not None}
    return areas if len(areas) == len(block_ids) else {}


class CpuJob(BaseModel):
    owner: str
    session_id: str
    thread_id: str | None = None
    view: CpuJobView
    run_ids: list[str] = Field(default_factory=list)
    research_usd: dict[str, float] = Field(default_factory=dict)
    # What an upgrade needs from a passed job, saved at CPU v1 so a restart loses nothing: the block winner texts,
    # their measures and the core winner text.
    winners: dict[str, str] = Field(default_factory=dict)
    measures: dict[str, Measure] = Field(default_factory=dict)
    core_text: str | None = None
    # An upgrade job keeps the parent's CPU v1 profiles for its comparison.
    parent_profile: CpuProfile | None = None
    parent_flash_profile: CpuProfile | None = None


@dataclass(slots=True)
class RvfiOutcome:
    """riscv-formal on one core, by code from the tool's check rows. ran is False when no check produced an answer."""

    ran: bool = False
    passed: int | None = None
    total: int | None = None
    timeouts: int | None = None
    failed: list[str] | None = None


def _apply_rvfi(target: Any, outcome: RvfiOutcome) -> None:
    """Write a riscv-formal outcome onto a version or an evidence row (all null when it did not run)."""
    target.rvfi_passed, target.rvfi_total = outcome.passed, outcome.total
    target.rvfi_timeouts, target.rvfi_failed = outcome.timeouts, outcome.failed


def _keep_rvfi(old: CpuEvidence, new: CpuEvidence) -> CpuEvidence:
    """Re-reading a run's sign-off sheet never takes a core's riscv-formal numbers away."""
    for name in ("rvfi_passed", "rvfi_total", "rvfi_timeouts", "rvfi_failed"):
        setattr(new, name, getattr(old, name))
    return new


class CpuRunStore:
    """A CPU job takes no credit, so its runs pass the credit checkpoints. Delegate all run persistence and spend unchanged."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    async def get_credits(self, owner: str, default: int = 3) -> int:
        return 1


class CpuJobManager:
    def __init__(self, settings: Settings, store: Any, runs: RunManager) -> None:
        self.settings, self.store, self.runs = settings, store, runs
        self._tasks: dict[str, asyncio.Task] = {}
        self._admission = asyncio.Lock()

    async def _active_jobs(self) -> list[CpuJob]:
        # The store's own active filter predates the improving status, so filter here.
        return [j for j in await self.store.list_cpu_jobs() if j.view.status in ACTIVE_JOB_STATUSES]

    async def start(self) -> None:
        for job in await self._active_jobs():
            await self._refresh(job)
            self._stopped(job, "server restarted")
            await self.store.save_cpu_job(job)

    @staticmethod
    def _stopped(job: CpuJob, reason: str) -> None:
        job.view.status = "stopped"
        job.view.message = reason
        job.view.finished_at = _now()
        for row in [*job.view.blocks, job.view.core]:
            if row.status == "running":
                row.status = "failed"

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its coroutine starts never enters its finally block.
        for job in await self._active_jobs():
            self._stopped(job, "server shutting down")
            await self.store.save_cpu_job(job)
        self._tasks.clear()

    async def _admit(self, pack_ids: Sequence[str]) -> None:
        """Everything that must hold before a CPU job starts. Call under _admission. A CPU job takes no credit: only a
        signed-in visitor reaches it, and signed-in visitors are never charged. The dollar caps and slots still apply."""
        # Fail before charging if the CPU packs have not been installed.
        for pack_id in pack_ids:
            pack = self.runs._packs.get(pack_id)
            if pack is None or not pack.eval_only:
                raise UnknownPreset("CPU packs are not installed")
            pack.reverify()
        if self.runs._closing or self.runs._maintenance():
            raise AdmissionDenied("maintenance", "live runs are offline")
        problem = await self.runs._sandbox_problem()
        if problem:
            raise AdmissionDenied("sandbox_down", problem)
        daily = await self.runs._daily_spend()
        if daily.exhausted or not daily.can_admit(self.runs._admission_cap(min(0.50, self.settings.cpu_job.max_usd))):
            raise AdmissionDenied("budget_exhausted", "the daily budget is used up")

    async def create(
        self, request: CreateCpuJobRequest, *, session_id: str,
        role: str, email: str | None = None,
    ) -> CpuJobView:
        if not self.settings.can_build(role):
            raise Forbidden(SIGN_IN_TO_BUILD)
        async with self._admission:
            await self._admit([*(p for p, _ in BLOCKS), "rv32i_core"])
            job_id = "cpu_" + uuid.uuid4().hex
            view = CpuJobView(
                id=job_id, goal=request.goal, created_at=_now(),
                blocks=[CpuBlockView(block_id=p, pack_id=p, title=self.runs._packs[p].title or title) for p, title in BLOCKS],
            )
            job = CpuJob(owner=spend_owner(session_id), session_id=session_id, thread_id=request.thread_id, view=view)
            await self.store.save_cpu_job(job)
            self._tasks[job_id] = asyncio.create_task(self._drive(job), name=f"verity-cpu-{job_id}")
            return view.model_copy(deep=True)

    async def upgrade(
        self, job_id: str, request: UpgradeRequest, *, session_id: str,
        role: str, email: str | None = None,
    ) -> CpuJobView:
        """The rules of check_upgrade, then admits like create."""
        parent, spec = await self.check_upgrade(
            job_id, request.upgrade_id, session_id=session_id,
            admin=self.settings.is_admin(email, role), can_build=self.settings.can_build(role),
        )
        async with self._admission:
            await self._admit([spec.new_block, spec.core_pack])
            child_id = "cpu_" + uuid.uuid4().hex
            new = self.runs._packs[spec.new_block]
            blocks = [self._reused_row(parent, p) for p in spec.reused]
            blocks.append(CpuBlockView(block_id=spec.new_block, pack_id=spec.new_block, title=new.title or spec.new_block, research="skipped"))
            version = parent.view.versions[0] if parent.view.versions else None
            view = CpuJobView(
                id=child_id, spec_id=spec.spec_id, goal=parent.view.goal, created_at=_now(), blocks=blocks,
                parent_job_id=parent.view.id, upgrade_id=request.upgrade_id,
            )
            job = CpuJob(
                owner=spend_owner(session_id), session_id=session_id, view=view,
                thread_id=parent.thread_id if parent.owner == spend_owner(session_id) else None,
                winners=dict(parent.winners), measures=dict(parent.measures), core_text=parent.core_text,
                parent_profile=version.profile if version else None,
                parent_flash_profile=version.flash_profile if version else None,
            )
            await self.store.save_cpu_job(job)
            self._tasks[child_id] = asyncio.create_task(self._drive(job, spec), name=f"verity-cpu-{child_id}")
            return view.model_copy(deep=True)

    async def check_upgrade(
        self, job_id: str, upgrade_id: str, *, session_id: str, admin: bool, can_build: bool
    ) -> tuple[CpuJob, UpgradeJob]:
        """The rules of an upgrade, shared by POST /cpu-jobs/{id}/upgrade and the chat agent's
        propose_cpu_upgrade: owner or admin of the parent (`admin`), and signed in (`can_build`, as create). Raises
        NotFound, Forbidden or Conflict with a plain reason when the parent has not passed or the option cannot be built.
        Spends nothing."""
        parent = await self.store.get_cpu_job(job_id)
        if parent is None:
            raise NotFound(job_id)
        if parent.owner != spend_owner(session_id) and not admin:
            raise Forbidden("This CPU job belongs to another account.")
        if not can_build:
            raise Forbidden(SIGN_IN_TO_BUILD)
        spec = UPGRADE_JOBS.get(upgrade_id)
        option = CATALOG.get(upgrade_id)
        if spec is None or option is None or not option.buildable:
            raise Conflict("Not buildable yet")
        if parent.view.spec_id != "rv32i_micro":
            raise Conflict("Only a passed RV32I core can be upgraded.")
        if parent.view.status != "passed":
            raise Conflict("Upgrades start from a passed CPU job. This one has not passed.")
        if not all(p in parent.winners for p, _ in BLOCKS) or not parent.core_text:
            raise Conflict("This CPU job has no saved block winners, so it cannot be upgraded.")
        return parent, spec

    async def check_retry(
        self, job_id: str, *, session_id: str, admin: bool, can_build: bool,
    ) -> tuple[CpuJob, dict[str, str], dict[str, Measure]]:
        """The rules of a core retry, shared by POST /cpu-jobs/{id}/retry and the chat agent's
        propose_cpu_retry: owner or admin of the job (`admin`), and signed in (`can_build`, as create); a plain RV32I job that failed, or was stopped
        (a server restart) before its core passed, with all 7 blocks passed and reusable; no retry of it running or
        passed. Raises NotFound, Forbidden or Conflict with a plain reason. Spends nothing. Returns the job and its
        saved block winners and measures."""
        failed = await self.store.get_cpu_job(job_id)
        if failed is None:
            raise NotFound(job_id)
        if failed.owner != spend_owner(session_id) and not admin:
            raise Forbidden("This CPU job belongs to another account.")
        if not can_build:
            raise Forbidden(SIGN_IN_TO_BUILD)
        if failed.view.spec_id != "rv32i_micro" or failed.view.upgrade_id:
            raise Conflict("Only a plain RV32I core job can retry its core.")
        stopped = failed.view.status == "stopped" and failed.view.core.status != "passed"
        if failed.view.status != "failed" and not stopped:
            raise Conflict("A core retry starts from a failed CPU job. This one has not failed.")
        rows = {r.block_id: r for r in failed.view.blocks}
        if not all(p in rows and rows[p].status == "passed" for p, _ in BLOCKS):
            raise Conflict("A core retry needs all 7 blocks passed. This job has a block that did not pass.")
        await self._retry_conflict(job_id)
        winners, measures = await self._saved_winners(failed)
        if winners is None or measures is None:
            raise Conflict("This job's blocks cannot be reused.")
        return failed, winners, measures

    async def _retry_conflict(self, job_id: str) -> None:
        """A core retry of this job that is still running, or one that passed, refuses another."""
        for job in await self.store.list_cpu_jobs():
            if job.view.retry_of != job_id:
                continue
            if job.view.status in ACTIVE_JOB_STATUSES:
                raise Conflict("A core retry is already running.")
            if job.view.status == "passed":
                raise Conflict(f"This job was already retried: CPU job {job.view.id}.")

    async def retry(
        self, job_id: str, *, session_id: str, role: str, email: str | None = None,
    ) -> CpuJobView:
        """Rerun only the integrated core of a failed plain job whose seven blocks all passed. The
        rules of check_retry, then admits like create. The new job reuses every block row and runs no block."""
        failed, winners, measures = await self.check_retry(
            job_id, session_id=session_id,
            admin=self.settings.is_admin(email, role), can_build=self.settings.can_build(role),
        )
        async with self._admission:
            await self._retry_conflict(job_id)  # again under the lock: a second press may have started one meanwhile
            await self._admit(["rv32i_core"])
            new_id = "cpu_" + uuid.uuid4().hex
            view = CpuJobView(
                id=new_id, goal=failed.view.goal, created_at=_now(),
                blocks=[self._reused_row(failed, p) for p, _ in BLOCKS], retry_of=job_id,
            )
            job = CpuJob(
                owner=spend_owner(session_id), session_id=session_id, view=view,
                thread_id=failed.thread_id if failed.owner == spend_owner(session_id) else None,
                winners=winners, measures=measures,
            )
            await self.store.save_cpu_job(job)
            self._tasks[new_id] = asyncio.create_task(self._drive(job), name=f"verity-cpu-{new_id}")
            return view.model_copy(deep=True)

    async def _saved_winners(self, job: CpuJob) -> tuple[dict[str, str] | None, dict[str, Measure] | None]:
        """The seven block winners and their measures of a job, as saved. A job that failed before they were saved is
        rebuilt from each block row's run with the rules block() uses; (None, None) when any block cannot be rebuilt."""
        ids = [p for p, _ in BLOCKS]
        if all(p in job.winners for p in ids):
            return dict(job.winners), dict(job.measures)
        winners: dict[str, str] = {}
        measures: dict[str, Measure] = {}
        for row in job.view.blocks:
            if not row.run_id:
                return None, None
            rtl = await self.store.read_run_file(row.run_id, "final/winner.v")
            if not rtl:
                return None, None
            text = rtl.decode("utf-8")
            if row.used_version == "v1":
                fallback = await self._v1_fallback(row.run_id)
                if fallback is None:
                    return None, None
                text, measure = fallback
            else:
                _, measure = await self._inspect(row.run_id)
            winners[row.block_id], measures[row.block_id] = text, measure
        if not all(p in winners for p in ids):
            return None, None
        return winners, measures

    @staticmethod
    def _reused_row(parent: CpuJob, pack_id: str) -> CpuBlockView:
        """The parent's block as a passed row of the child: no run is made, the run id and evidence stay the parent's."""
        row = next(r for r in parent.view.blocks if r.block_id == pack_id).model_copy(deep=True)
        row.status, row.reused, row.attempts = "passed", True, 0
        return row

    async def get(self, job_id: str, *, session_id: str, role: str, email: str | None = None) -> CpuJobView:
        job = await self.store.get_cpu_job(job_id)
        if job is None:
            raise NotFound(job_id)
        if job.owner != spend_owner(session_id) and not self.settings.is_admin(email, role):
            raise Forbidden("This CPU job belongs to another account.")
        await self._refresh(job)
        return _with_thread(job, session_id, await self._owner_retries(job, session_id))

    async def _owner_retries(self, job: CpuJob, session_id: str) -> dict[str, str]:
        """_newest_retries over the owner's jobs, read only when the reader is the owner (no one else is shown them)."""
        if job.owner != spend_owner(session_id):
            return {}
        return _newest_retries(await self.store.list_cpu_jobs(job.owner))

    async def mine(self, session_id: str) -> list[CpuJobView]:
        jobs = await self.store.list_cpu_jobs(spend_owner(session_id))
        for job in jobs:
            await self._refresh(job)
        retries = _newest_retries(jobs)
        return [_with_thread(job, session_id, retries) for job in jobs]

    async def recent(self, session_id: str, skip: Collection[str], limit: int) -> list[CpuJobView]:
        """This owner's newest CPU jobs other than `skip`, at most `limit`, for the chat agent's <session>. Only the jobs
        returned are refreshed, so a message costs the same however many jobs the owner has made."""
        every = await self.store.list_cpu_jobs(spend_owner(session_id))
        jobs = [j for j in every if j.view.id not in skip][: max(0, limit)]
        for job in jobs:
            await self._refresh(job)
        retries = _newest_retries(every)
        return [_with_thread(job, session_id, retries) for job in jobs]

    async def view(self, job_id: str, session_id: str) -> CpuJobView | None:
        """One job's view with no access check (the caller has made it), for the chat agent's facts about the job on
        screen. Its chat id and newest retry are set for the owner only, as in get."""
        job = await self.store.get_cpu_job(job_id)
        if job is None:
            return None
        await self._refresh(job)
        return _with_thread(job, session_id, await self._owner_retries(job, session_id))

    async def link_thread(self, job_id: str, session_id: str, thread_id: str) -> bool:
        """Point an owner's job at a chat, for a job whose own chat is gone. Another owner's job (an admin looking at it)
        is never linked. A running job is left alone: its task holds the live record and saves it whole."""
        job = await self.store.get_cpu_job(job_id)
        if job is None or job.owner != spend_owner(session_id) or job.thread_id == thread_id or job_id in self._tasks:
            return False
        job.thread_id = thread_id
        await self.store.save_cpu_job(job)
        return True

    async def for_thread(self, session_id: str, thread_id: str) -> list[CpuJobView]:
        """This owner's CPU jobs started from one chat, oldest first, for the chat agent's facts."""
        every = await self.store.list_cpu_jobs(spend_owner(session_id))
        jobs = [j for j in every if j.thread_id == thread_id]
        for job in jobs:
            await self._refresh(job)
        retries = _newest_retries(every)
        return [job.view.model_copy(update={"retry_job_id": retries.get(job.view.id)}) for job in reversed(jobs)]

    async def core_notes(self, job_id: str, *, diagnosis: bool = True) -> list[list[str]]:
        """core_failure_notes of each ended core run of a job whose core failed, oldest first (the core gets two tries).
        No access check: the caller passes a job it may read. Empty when the core did not fail."""
        job = await self.store.get_cpu_job(job_id)
        if job is None or job.view.core.status != "failed" or not job.view.core.pack_id:
            return []
        out: list[list[str]] = []
        for run_id in job.run_ids:
            run = await self.runs.get_run(run_id)
            if run is None or run.preset_id != job.view.core.pack_id or run.status in ACTIVE_STATUSES:
                continue
            notes = await core_failure_notes(self.store, run_id, diagnosis=diagnosis)
            if notes:
                out.append(notes)
        return out

    async def _prior_attempt(self, job_id: str) -> list[str]:
        """What a core retry's first RTL generation is told about the failed job's core: its sandbox and test output
        only, never the model's diagnosis. Each line once, at most PRIOR_LINES. Empty when nothing was recorded."""
        try:
            attempts = await self.core_notes(job_id, diagnosis=False)
        except Exception:
            log.exception("CPU job: could not read the failed core of %s", job_id)
            return []
        lines: list[str] = []
        for line in (ln for notes in attempts for ln in notes):
            if line not in lines:
                lines.append(line)
        return lines[:PRIOR_LINES]

    async def _refresh(self, job: CpuJob) -> None:
        spent = 0.0
        for run_id in job.run_ids:
            run = await self.runs.get_run(run_id)
            if run is not None:
                live = self.runs._live.get(run_id)
                spent += live.ctx.ledger.snapshot().usd if live is not None else run.spent.usd
        job.view.spent_usd = spent + sum(job.research_usd.values())
        for row in [*job.view.blocks, job.view.core]:
            if row.run_id:
                row.summary = await self.store.get_run_summary(row.run_id)
                if row.evidence.eqy_vs_v1 is None and not getattr(row, "reused", False) and row.run_id not in self.runs._live:
                    old = row.evidence
                    row.evidence, _ = await self._inspect(row.run_id)
                    if isinstance(row, CpuCoreView):
                        _keep_rvfi(old, row.evidence)
        for version in job.view.versions:
            if version.core_run_id:
                version.core_summary = await self.store.get_run_summary(version.core_run_id)

    async def _inspect(self, run_id: str) -> tuple[CpuEvidence, Measure]:
        """Evidence and measured numbers of an ended run's winner, read by code from the run record, its
        candidate rows and its sign-off sheet. Nothing here comes from a model."""
        run = await self.runs.get_run(run_id)
        if run is None or not run.winner_id:
            return CpuEvidence(), Measure(run_id=run_id)
        evidence = CpuEvidence(repairs=run.spent.repairs, techniques=await self._techniques(run_id))
        measure = Measure(run_id=run_id)
        try:
            cands = await self.store.candidates_for_run(run_id)
        except Exception:
            log.exception("CPU job: could not read candidates of %s", run_id)
            cands = []
        win = next((c for c in cands if c.id == run.winner_id), None)
        if win is not None:
            measure = Measure(run_id=run_id, area_um2=win.area_um2, clock_ns=win.clock_period_ns, power_mw=win.power_mw)
        sheet = await self._signoff(run_id, run.winner_id)
        if sheet is not None:
            evidence.eqy_vs_v1 = sheet.eqy_vs_v1
            evidence.seeds_passed, evidence.seeds_run = sheet.tests.seeds_passed, sheet.tests.seeds_run
            if sheet.tests.per_seed:
                # The first tally is the winner's own test run; the rest are the extra seeds.
                evidence.tests_passed, evidence.tests_total = sheet.tests.per_seed[0].passed, sheet.tests.per_seed[0].total
            if sheet.strength is not None and sheet.strength.status == "ok":
                evidence.mutants_killed, evidence.mutants_total = sheet.strength.caught, sheet.strength.planted
        return evidence, measure

    async def _v1_fallback(self, run_id: str) -> tuple[str, Measure] | None:
        """A CPU block winner must be proven equal to its own v1. When it is not, the CPU uses v1 itself: the
        baseline candidate's last attempt that passed the locked tests, with v1's measured numbers. None if absent."""
        try:
            base = next((c for c in await self.store.candidates_for_run(run_id) if c.is_baseline), None)
        except Exception:
            log.exception("CPU job: could not read candidates of %s", run_id)
            return None
        if base is None:
            return None
        text: str | None = None
        for n in range(1, 21):
            rtl = await self.store.read_run_file(run_id, f"candidates/{base.id}/attempt-{n}.v")
            if rtl is None:
                break
            raw = await self.store.read_run_file(run_id, f"candidates/{base.id}/attempt-{n}.verify.json")
            try:
                passed = raw is not None and json.loads(raw).get("status") == "pass"
            except ValueError:
                passed = False
            if passed:
                text = rtl.decode("utf-8")
        if text is None:
            return None
        return text, Measure(run_id=run_id, area_um2=base.area_um2, clock_ns=base.clock_period_ns, power_mw=base.power_mw)

    async def _signoff(self, run_id: str, winner_id: str) -> Signoff | None:
        for name in ("final/signoff.json", f"signoff/{winner_id}.json"):
            try:
                raw = await self.store.read_run_file(run_id, name)
                sheet = Signoff.model_validate_json(raw) if raw else None
            except Exception:
                continue
            if sheet is not None and sheet.candidate_id == winner_id:
                return sheet
        return None

    async def _techniques(self, run_id: str) -> list[str] | None:
        """Technique ids this run's versions were asked to try (ids from the fixed list only), or None."""
        try:
            outcome = await self.store.get_run_outcome(run_id)
            if outcome is None:
                return None
            rows = await self.store.strategy_outcomes(outcome.key, outcome.pack_sha256, outcome.tool_image_digest)
        except Exception:
            return None
        return sorted({r.technique for r in rows if r.run_id == run_id and r.technique})

    async def _bench(
        self, rtl: Rtl, pack: TaskPack, wait_cycles: int, *, clock_ns: float | None, areas: dict[str, float],
    ) -> tuple[CpuProfile | None, str]:
        """The benchmark suite on one core and one memory mode, as a profile built by code. The second value is the
        tool's status ("none" when it could not run at all). A profile is None unless every benchmark passed."""
        runner = getattr(self.runs._tools, "bench", None)
        if runner is None:
            return None, "none"
        try:
            result = await runner(rtl, pack, wait_cycles)
        except Exception:
            log.exception("CPU job: benchmark run failed on %s at wait %d", pack.id, wait_cycles)
            return None, "none"
        if result.status != "pass":
            return None, result.status
        try:
            return result.profile(clock_ns=clock_ns, area_by_block_um2=areas), "pass"
        except ValueError:
            log.exception("CPU job: benchmark result of %s could not become a profile", pack.id)
            return None, "none"

    async def _rvfi(self, rtl: Rtl, pack: TaskPack) -> RvfiOutcome:
        runner = getattr(self.runs._tools, "rvfi", None)
        if runner is None:
            return RvfiOutcome()
        try:
            result = await runner(rtl, pack)
        except Exception:
            log.exception("CPU job: riscv-formal failed to run on %s", pack.id)
            return RvfiOutcome()
        checks = result.checks
        if not any(c.status != "error" for c in checks):
            return RvfiOutcome()
        return RvfiOutcome(
            ran=True, passed=sum(c.status == "pass" for c in checks), total=len(checks),
            timeouts=sum(c.status == "timeout" for c in checks), failed=[c.name[:60] for c in checks if c.status == "fail"],
        )

    async def _profile_version(
        self, version: CpuVersionView, pack: TaskPack, wins: Mapping[str, Measure], block_ids: Sequence[str],
        *, flash: bool, core_clock_ns: float | None,
    ) -> RvfiOutcome:
        """Bench the core winner at wait 0 (and wait 3 where the core supports it) and run riscv-formal. Everything
        that could not be measured stays null with a fixed note; nothing is guessed. Never raises."""
        raw = await self.store.read_run_file(version.core_run_id, "final/winner.v") if version.core_run_id else None
        try:
            rtl = Rtl.validate(raw.decode("utf-8"), top=pack.top, max_lines=pack.max_lines) if raw else None
        except (RtlRejected, UnicodeDecodeError):
            rtl = None
        if rtl is None:
            version.profile_note = "The core winner could not be read, so nothing was measured."
            return RvfiOutcome()
        notes = []
        # Benchmark time is at the integrated core's measured clock (its critical path crosses blocks), not a block's.
        areas, clock = area_by_block(wins, block_ids), core_clock_ns
        version.profile, _ = await self._bench(rtl, pack, 0, clock_ns=clock, areas=areas)
        if version.profile is None:
            notes.append("The benchmarks did not finish, so speed is not measured.")
        if flash:
            version.flash_profile, status = await self._bench(rtl, pack, FLASH_WAIT_CYCLES, clock_ns=clock, areas=areas)
            if version.flash_profile is None:
                notes.append("Benchmarks with flash-like memory did not finish." if status != "not_supported"
                             else "This core cannot run with memory wait cycles.")
        else:
            version.flash_profile = None
            notes.append("This core cannot run with memory wait cycles.")
        outcome = await self._rvfi(rtl, pack)
        if not outcome.ran:
            notes.append("riscv-formal could not run, so it is not measured.")
        version.profile_note = " ".join(notes) or None
        _apply_rvfi(version, outcome)
        _apply_rvfi(version.core_evidence, outcome)
        return outcome

    async def _drive(self, job: CpuJob, upgrade: UpgradeJob | None = None) -> None:
        cfg = self.settings.cpu_job
        workers: list[asyncio.Task] = []
        # Retain actual spend for ended attempts, and each live run's full cap. Research spend sits here too.
        allocations: dict[str, float] = {f"research:{k}": v for k, v in job.research_usd.items()}
        inflight: set[str] = set()
        budget_changed = asyncio.Event()
        launch = asyncio.Lock()
        width = asyncio.Semaphore(cfg.width)
        winners: dict[str, str] = {}
        wins: dict[str, Measure] = {}
        if upgrade is not None:
            # The reused blocks come from the parent's saved winners; only the new block is run.
            winners = {p: job.winners[p] for p in upgrade.reused}
            wins = {p: job.measures[p] for p in upgrade.reused if p in job.measures}
        elif job.view.retry_of:
            # A core retry: every block is a reused row, so the winners and what each block tried come from the failed job.
            winners, wins = dict(job.winners), dict(job.measures)
        research_ids: dict[str, str | None] = {}
        tried: dict[str, set[str]] = {}
        if job.view.retry_of:
            # An improve round reads the block's research as in a plain job (a research id not owned here is ignored).
            research_ids = {r.block_id: r.research_id for r in job.view.blocks}
            tried = {r.block_id: set(r.evidence.techniques or []) for r in job.view.blocks}

        async def run_pack(pack_id: str, mode: str, row: Any, research_id: str | None = None, **extra: Any) -> RunRecord:
            async with launch:
                available = cfg.max_usd - sum(allocations.values())
                while available <= 1e-9:
                    if not inflight:
                        raise AdmissionDenied("budget_exhausted", "CPU job spend cap reached")
                    # An ending run releases the unused part of its cap. Wait before
                    # deciding that reserved dollars have actually been spent.
                    budget_changed.clear()
                    await budget_changed.wait()
                    available = cfg.max_usd - sum(allocations.values())
                async def admit() -> RunRecord:
                    for attempt in range(SANDBOX_RETRIES + 1):
                        try:
                            return await self.runs.start_cpu_run(
                                session_id=job.session_id, preset_id=pack_id, priority=job.view.goal,
                                mode=mode, max_usd=min(RUN_CAP_USD, available), thread_id=job.thread_id,
                                research_id=research_id, **extra,
                            )
                        except AdmissionDenied as exc:
                            if exc.reason != "sandbox_down" or attempt == SANDBOX_RETRIES:
                                raise
                            log.warning("CPU job %s: sandbox not answering, retry %d for %s", job.view.id, attempt + 1, pack_id)
                            await asyncio.sleep(SANDBOX_RETRY_S)
                    raise AssertionError("unreachable")

                starting = asyncio.create_task(admit())
                cancelled = False
                try:
                    run = await asyncio.shield(starting)
                except asyncio.CancelledError:
                    # Admission may already have launched the run before attaching its thread.
                    # Retain its id so the job's cleanup can stop it too.
                    cancelled = True
                    run = await starting
                allocations[run.id] = run.budget.max_usd
                inflight.add(run.id)
                job.run_ids.append(run.id)
                row.run_id = run.id
                row.status = "running"
                if isinstance(row, CpuBlockView):
                    row.attempts += 1
                await self.store.save_cpu_job(job)
                if cancelled:
                    raise asyncio.CancelledError
            result = await self.runs.wait_for_run(run.id)
            allocations[run.id] = result.spent.usd
            inflight.remove(run.id)
            budget_changed.set()
            await self._refresh(job)
            return result

        async def research(row: CpuBlockView) -> str | None:
            """One research per block through the same ResearchService as a chip run. Never blocks the block."""
            service = self.runs.research
            if service is None:
                row.research = "off"
                return None
            try:
                if cfg.max_usd - sum(allocations.values()) < service.worst_case_usd():
                    row.research = "skipped"
                    return None
                outcome = await service.lookup_or_search(
                    cpu_block_research(row.block_id, job.view.goal), session_id=job.session_id,
                )
            except Exception:
                log.exception("CPU job %s: research for %s failed", job.view.id, row.block_id)
                row.research = "failed"
                return None
            row.research = outcome.state if outcome.state in ("ran", "reused", "skipped", "failed") else "failed"
            if outcome.research is None or outcome.state not in ("ran", "reused"):
                return None
            usd = outcome.research.usd
            if usd > 0:
                job.research_usd[row.block_id] = usd
                allocations[f"research:{row.block_id}"] = usd
            row.research_id = outcome.research.id
            return outcome.research.id

        async def block(row: CpuBlockView) -> None:
            async with width:
                for _ in range(cfg.block_retry + 1):
                    result = await run_pack(row.pack_id, "full", row, research_id=research_ids.get(row.block_id))
                    if result.status in ("done", "cap_reached") and result.winner_id:
                        rtl = await self.store.read_run_file(result.id, "final/winner.v")
                        if rtl:
                            row.evidence, measure = await self._inspect(result.id)
                            text = rtl.decode("utf-8")
                            row.used_version = None
                            if row.evidence.eqy_vs_v1 not in ("proven", "v1_is_winner"):
                                fallback = await self._v1_fallback(result.id)
                                if fallback is None:
                                    log.warning("CPU job %s: %s winner not proven and no passing v1", job.view.id, row.block_id)
                                    row.status = "failed"
                                    await self.store.save_cpu_job(job)
                                    continue
                                text, measure = fallback
                                row.used_version = "v1"
                            winners[row.block_id], wins[row.block_id] = text, measure
                            tried[row.block_id] = set(row.evidence.techniques or [])
                            row.status = "passed"
                            await self.store.save_cpu_job(job)
                            return
                    row.status = "failed"
                    await self.store.save_cpu_job(job)
                raise RuntimeError(f"{row.block_id} ended without a passing winner")

        async def improve() -> None:
            """The improve rounds, run by code. Stops at the first round that keeps nothing."""
            goal = job.view.goal
            for number in range(2, 2 + cfg.improve_rounds):
                pick = pick_block(goal, wins)
                if pick is None:
                    job.view.message = f"No improve round: no block reported its {goal} number."
                    return
                if cfg.max_usd - sum(allocations.values()) < 2 * RUN_CAP_USD:
                    job.view.message = f"Improve stopped before CPU v{number}: the job spend cap is nearly used."
                    return
                scratch = CpuBlockView(block_id=pick, pack_id=pick, title=pick)
                try:
                    result = await run_pack(
                        pick, "full", scratch, research_id=research_ids.get(pick),
                        candidate_multiplier=2, skip_techniques=sorted(tried.get(pick, ())),
                    )
                except AdmissionDenied:
                    job.view.message = f"Improve stopped before CPU v{number}: the job spend cap is used."
                    return
                evidence, measure = (CpuEvidence(), Measure())
                rtl = None
                if result.status in ("done", "cap_reached") and result.winner_id:
                    evidence, measure = await self._inspect(result.id)
                    rtl = await self.store.read_run_file(result.id, "final/winner.v")
                tried.setdefault(pick, set()).update(evidence.techniques or [])
                if not rtl or evidence.eqy_vs_v1 != "proven" or not is_better(goal, measure, wins[pick]):
                    job.view.message = f"Improve stopped: {pick} found nothing better on {goal} that EQY proves equal to its own v1."
                    return
                trial = {**winners, pick: rtl.decode("utf-8")}
                pack = self._derive(job, trial, version=number)
                self.runs.register_custom_pack(pack)
                core_row = CpuCoreView(pack_id=pack.id)
                core = await run_pack(pack.id, "fast", core_row)
                if not (core.status in ("done", "cap_reached") and core.winner_id):
                    job.view.message = f"Improve stopped: the core did not pass with the better {pick}, so CPU v{number} was dropped."
                    return
                winners.update(trial)
                wins[pick] = measure
                core_evidence, core_measure = await self._inspect(core.id)
                version = CpuVersionView(
                    version=number, core_run_id=core.id, changed_block=pick, block_run_id=result.id,
                    totals=version_totals(wins), core_evidence=core_evidence, block_evidence=evidence,
                )
                job.view.versions.append(version)
                await self._profile_version(
                    version, pack, wins, [p for p, _ in BLOCKS], flash=True, core_clock_ns=core_measure.clock_ns,
                )
                job.view.message = None
                await self._refresh(job)
                await self.store.save_cpu_job(job)

        async def research_block(row: CpuBlockView) -> None:
            async with width:
                research_ids[row.block_id] = await research(row)

        try:
            todo = [row for row in job.view.blocks if not row.reused]
            if upgrade is None:
                # All seven researches finish first, so their spend is counted before any run reserves dollars.
                await asyncio.gather(*(research_block(row) for row in todo))
            await self.store.save_cpu_job(job)
            workers = [asyncio.create_task(block(row)) for row in todo]
            await asyncio.gather(*workers)
            if upgrade is None:
                # Kept as soon as all seven passed, so a core that then fails leaves them for a core retry.
                job.winners, job.measures = dict(winners), dict(wins)
            job.view.status = "integrating"
            await self.store.save_cpu_job(job)
            if upgrade is None:
                pack = self._derive(job, winners)
            else:
                pack = self._derive_upgrade(job, upgrade, winners)
            self.runs.register_custom_pack(pack)
            job.view.core.pack_id = pack.id
            # A core retry tells the core's first RTL generation what the failed job's core checks reported (sandbox
            # and test output only, written by code).
            prior = await self._prior_attempt(job.view.retry_of) if job.view.retry_of and upgrade is None else []
            told = {"prior_attempt": prior} if prior else {}
            # The core gets the same one retry as a block: a first draft that fails every program and whose repairs
            # break lint should not sink a job whose seven blocks all passed (seen on a real job, 2026-10-28).
            for _ in range(cfg.block_retry + 1):
                result = await run_pack(pack.id, "fast", job.view.core, **told)
                if result.status in ("done", "cap_reached") and result.winner_id:
                    break
            passed = result.status in ("done", "cap_reached") and bool(result.winner_id)
            job.view.core.status = "passed" if passed else "failed"
            if not passed:
                job.view.status = "failed"
                job.view.message = "Core run ended without a passing winner."
            else:
                job.view.core.evidence, core_measure = await self._inspect(result.id)
                block_ids = [*upgrade.reused, upgrade.new_block] if upgrade else [p for p, _ in BLOCKS]
                version = CpuVersionView(
                    version=1, core_run_id=result.id, totals=version_totals(wins, block_ids),
                    core_evidence=job.view.core.evidence,
                )
                job.view.versions = [version]
                await self.store.save_cpu_job(job)
                outcome = await self._profile_version(
                    version, pack, wins, block_ids, flash=upgrade.flash if upgrade else True,
                    core_clock_ns=core_measure.clock_ns,
                )
                _apply_rvfi(job.view.core.evidence, outcome)
                if upgrade is None:
                    # Saved at CPU v1 (improve rounds change the live dicts), so a passed job can be upgraded later.
                    core_text = await self.store.read_run_file(result.id, "final/winner.v")
                    job.winners, job.measures = dict(winners), dict(wins)
                    job.core_text = core_text.decode("utf-8") if core_text else None
                    job.view.status = "improving" if cfg.improve_rounds > 0 else "passed"
                else:
                    self._finish_upgrade(job, upgrade, version, outcome)
                await self.store.save_cpu_job(job)
                try:
                    if upgrade is None and cfg.improve_rounds > 0:
                        await improve()
                except Exception as exc:
                    # CPU v1 already passed. A failed improve round never takes that result away.
                    log.exception("CPU job %s: improve rounds failed", job.view.id)
                    job.view.message = "Improve rounds stopped early."
                    if isinstance(exc, AdmissionDenied):
                        job.view.message = "Improve rounds stopped: " + str(exc)
                if upgrade is None:
                    job.view.status = "passed"
        except asyncio.CancelledError:
            job.view.status, job.view.message = "stopped", "server shutting down"
        except Exception as exc:
            log.exception("CPU job %s failed", job.view.id)
            job.view.status = "failed"
            job.view.message = str(exc) if isinstance(exc, (AdmissionDenied, RuntimeError)) else "CPU job could not finish."
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()

            async def stop_runs() -> None:
                # Stop known runs first to free global slots for pending admission.
                for run_id in list(job.run_ids):
                    run = await self.runs.get_run(run_id)
                    if run is not None and run.status in ACTIVE_STATUSES:
                        await self.runs.stop(run_id, job.session_id)
                        await self.runs.wait_for_run(run_id)

            await stop_runs()
            await asyncio.gather(*workers, return_exceptions=True)
            # A shielded admission may have returned another id during cancellation.
            await stop_runs()
            await self._refresh(job)
            for row in job.view.blocks:
                if row.status == "running":
                    row.status = "failed"
            if job.view.core.status == "running":
                job.view.core.status = "failed"
            job.view.finished_at = _now()
            await self.store.save_cpu_job(job)
            self._tasks.pop(job.view.id, None)

    def _derive(self, job: CpuJob, winners: dict[str, str], version: int = 1) -> TaskPack:
        text = "\n\n".join(winners[p].rstrip() for p, _ in BLOCKS) + "\n"
        return self._write_pack(job, "rv32i_core", text, version)

    def _derive_upgrade(self, job: CpuJob, upgrade: UpgradeJob, winners: dict[str, str]) -> TaskPack:
        """blocks.v of an upgrade core: the parent's reused winners, the parent's core winner when the new top
        instantiates it (the cache), then the new block's winner."""
        parts = [winners[p] for p in upgrade.reused]
        if upgrade.parent_core:
            parts.append(job.core_text or "")
        parts.append(winners[upgrade.new_block])
        return self._write_pack(job, upgrade.core_pack, "\n\n".join(t.rstrip() for t in parts) + "\n")

    def _write_pack(self, job: CpuJob, static_id: str, blocks_text: str, version: int = 1) -> TaskPack:
        static = self.runs._packs[static_id]
        static.reverify()
        pack_id = f"{static_id}_" + job.view.id.removeprefix("cpu_")[:12] + (f"_v{version}" if version > 1 else "")
        root = self.settings.data_dir / "cpu_packs" / job.view.id / pack_id
        root.mkdir(parents=True, exist_ok=False)
        for rel in manifest_entries(static.root):
            # blocks.v is rewritten from the winners. reference.v is the hand-written top (never in a prompt or
            # the sandbox); the fake provider echoes it and the core's human-reference measurement reads it.
            if rel in (MANIFEST_NAME, "blocks.v"):
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(static.root / rel, target)
        task = root / "task.toml"
        text, replaced = re.subn(r'(?m)^id\s*=\s*[^\n]+', f'id = "{pack_id}"', task.read_text(encoding="utf-8"), count=1)
        if replaced != 1:
            raise RuntimeError("Core pack has no id")
        task.write_text(text, encoding="utf-8")
        (root / "blocks.v").write_text(blocks_text, encoding="utf-8")
        write_manifest(root)
        return TaskPack.load(root)

    @staticmethod
    def _finish_upgrade(job: CpuJob, upgrade: UpgradeJob, version: CpuVersionView, outcome: RvfiOutcome) -> None:
        """The new numbers next to the parent's CPU v1, then the riscv-formal gate where the upgrade has one."""
        job.view.comparison = compare_profiles(
            job.parent_profile, job.parent_flash_profile, version.profile, version.flash_profile,
        )
        job.view.status = "passed"
        if not upgrade.rvfi_gate:
            return
        if not outcome.ran:
            job.view.status, job.view.message = "failed", "riscv-formal could not run"
        elif outcome.failed:
            shown = ", ".join(outcome.failed[:RVFI_NAMES_SHOWN]) + (", ..." if len(outcome.failed) > RVFI_NAMES_SHOWN else "")
            job.view.status, job.view.message = "failed", f"riscv-formal failed: {shown}"
        elif outcome.timeouts:
            job.view.message = f"riscv-formal: {outcome.timeouts} of {outcome.total} checks not proven within the time limit."
