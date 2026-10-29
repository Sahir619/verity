"""Shared plumbing for the eval suites: the environment (settings, provider, tools, store) and one in-process
orchestrator run.

`run_pipeline` builds the same objects the RunManager builds (ledger, RunLLM, EventHub, RunContext) and calls
`run_orchestrator`, so an eval run goes through exactly the production path, minus HTTP and admission. Events land in a
throwaway SQLite store under evals/work/<eval_run_id>/.
"""

import asyncio
import logging
import subprocess
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..config import AGENT_ROOT, Settings, load_settings
from ..contracts import Candidate, CandidateEvent, EventBase, StageEvent, WinnerEvent
from ..core.budget import BudgetLimits, SpendSnapshot
from ..core.context import RunContext, RunOutcome
from ..core.pack import TaskPack, load_packs, verify_manifest
from ..core.protocols import LLMProvider, ToolRunner
from ..core.records import EvalResultRow, RunRecord
from ..llm.factory import build_provider
from ..llm.fake import FakeProvider
from ..llm.gateway import RunLLM
from ..orchestrator import run_orchestrator
from ..roles import default_roles
from ..runs.hub import EventHub
from ..runs.ledger import RunLedger
from ..store import SqliteStore
from ..tools.factory import build_tool_runner
from .record import EvalRecord, ResultFile

log = logging.getLogger("verity.evals")

EVALS_ROOT = AGENT_ROOT / "evals"
DEFAULT_RESULTS_DIR = EVALS_ROOT / "results"
DEFAULT_WORK_DIR = EVALS_ROOT / "work"


@dataclass(slots=True)
class EvalEnv:
    settings: Settings
    provider: LLMProvider
    provider_name: str
    tools: ToolRunner
    store: SqliteStore
    packs: dict[str, TaskPack]
    eval_run_id: str
    git_sha: str | None
    tool_image_digest: str
    results: ResultFile
    skip_sandbox: bool = False
    total_usd: float = 0.0
    max_total_usd: float = 3.0
    pipeline_cache: dict[tuple[str, int, str], "PipelineRun"] = field(default_factory=dict)

    def budget_left(self) -> bool:
        """False once the cumulative estimated spend of this eval run passed --max-total-usd."""
        return self.provider_name == "fake" or self.total_usd < self.max_total_usd

    def new_record(self, suite: str, task_id: str, repeat: int = 1, case: str | None = None) -> EvalRecord:
        return EvalRecord(
            eval_run_id=self.eval_run_id,
            git_sha=self.git_sha,
            task_id=task_id,
            suite=suite,
            provider=self.provider_name,
            repeat=repeat,
            case=case,
            model_ids=self.settings.models_by_role(),
            tool_image_digest=self.tool_image_digest,
        )

    async def write(self, record: EvalRecord) -> None:
        """Append to the JSONL file and (best effort) to the eval_results table."""
        if not record.runtime_s:  # pipeline rows set it from the run itself; every other suite uses the record clock
            record.runtime_s = round(record.elapsed_s, 2)
        pack = self.packs.get(record.task_id)
        if record.pack_hash_ok is None and pack is not None:
            record.pack_hash_ok = pack_unchanged(pack)
        self.total_usd += record.est_usd
        self.results.write(record)
        try:
            await self.store.add_eval_result(
                EvalResultRow(
                    eval_run_id=record.eval_run_id,
                    task_id=record.task_id,
                    suite=record.suite,
                    metrics=record.model_dump(mode="json"),
                    git_sha=record.git_sha,
                    created_at=datetime.now(UTC),
                )
            )
        except Exception:
            log.exception("could not store the eval result row")

    def limits_for(self, pack: TaskPack) -> BudgetLimits:
        return BudgetLimits.from_settings(self.settings, pack.budget)

    def new_llm(self, pack: TaskPack) -> tuple[RunLLM, RunLedger]:
        ledger = RunLedger(self.limits_for(pack), self.settings)
        return RunLLM(self.provider, self.settings, ledger), ledger

    async def aclose(self) -> None:
        for closer in (self.provider.aclose, self.store.close):
            try:
                await closer()
            except Exception:
                log.exception("eval environment cleanup failed")


def _git_sha() -> str | None:
    try:
        out = subprocess.run(  # noqa: S603, S607 - fixed argument list, no shell
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(AGENT_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        sha = out.stdout.strip()
        return sha or None
    except (OSError, subprocess.SubprocessError):
        return None


def new_eval_run_id() -> str:
    return "e_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]


async def open_env(
    provider_name: str,
    *,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    work_dir: Path = DEFAULT_WORK_DIR,
    inject_bug: bool = True,
    skip_sandbox: bool = False,
    max_total_usd: float = 3.0,
    tasks_dir: Path | None = None,
) -> EvalEnv:
    """Build everything a suite needs. `inject_bug` only matters for the fake provider (see llm/fake.py): the
    functional suite turns it off so a fake pass@1 means "the pipeline works", the pipeline suites keep it on so the
    repair path runs."""
    eval_run_id = new_eval_run_id()
    base = load_settings(provider=provider_name)
    plan = base.plan.model_copy(update={"gate_auto_continue_ms": 0, "gate_after_replan_ms": 0})
    overrides: dict[str, object] = {"data_dir": (work_dir / eval_run_id).resolve(), "plan": plan}
    if tasks_dir is not None:
        overrides["tasks_dir"] = tasks_dir.resolve()
    settings = base.model_copy(update=overrides)

    provider: LLMProvider
    if provider_name == "fake":
        provider = FakeProvider(settings, delay_s=0.0, inject_bug=inject_bug)
    else:
        provider = build_provider(settings)
    tools = build_tool_runner(settings)
    store = SqliteStore(settings)
    await store.open()
    packs = load_packs(settings.tasks_dir)

    digest = ""
    if not skip_sandbox:
        try:
            digest = await asyncio.wait_for(tools.image_digest(), timeout=30)
        except Exception:
            log.warning("could not read the tool image digest", exc_info=True)

    return EvalEnv(
        settings=settings,
        provider=provider,
        provider_name=provider_name,
        tools=tools,
        store=store,
        packs=packs,
        eval_run_id=eval_run_id,
        git_sha=await asyncio.to_thread(_git_sha),
        tool_image_digest=digest,
        results=ResultFile(results_dir, eval_run_id),
        skip_sandbox=skip_sandbox,
        max_total_usd=max_total_usd,
    )


def select_packs(env: EvalEnv, tasks: str) -> list[TaskPack]:
    """`all` or a comma separated list of pack ids (unknown ids raise ValueError)."""
    if tasks.strip().lower() == "all":
        return [env.packs[k] for k in sorted(env.packs)]
    chosen: list[TaskPack] = []
    for task_id in (t.strip() for t in tasks.split(",") if t.strip()):
        if task_id not in env.packs:
            raise ValueError(f"unknown task {task_id!r}; available: {', '.join(sorted(env.packs))}")
        chosen.append(env.packs[task_id])
    return chosen


# ======================================================================
# one orchestrator run
# ======================================================================
@dataclass(slots=True)
class PipelineRun:
    run_id: str
    events: list[EventBase]
    candidates: dict[str, Candidate]
    winner_id: str | None
    outcome: RunOutcome | None
    spend: SpendSnapshot
    wall_s: float
    stage_times: dict[str, float]
    prompt_versions: dict[str, str]
    pack_hash_ok: bool
    error: str | None = None
    llm_calls: list[dict] = field(default_factory=list)  # RunLLM.calls: one entry per model call

    @property
    def baseline(self) -> Candidate | None:
        return self.candidates.get("c1")

    @property
    def winner(self) -> Candidate | None:
        return self.candidates.get(self.winner_id) if self.winner_id else None


def stage_times_from(events: list[EventBase]) -> dict[str, float]:
    """Seconds spent per stage: sum of (done|failed|skipped t_ms) minus (active t_ms), per stage, over all rounds."""
    started: dict[str, int] = {}
    totals: dict[str, float] = defaultdict(float)
    for event in events:
        if not isinstance(event, StageEvent):
            continue
        if event.status == "active":
            started[event.stage] = event.t_ms
        elif event.status in ("done", "failed", "skipped") and event.stage in started:
            totals[event.stage] += max(0, event.t_ms - started.pop(event.stage)) / 1000
    return {stage: round(seconds, 3) for stage, seconds in totals.items()}


def last_candidates(events: list[EventBase]) -> dict[str, Candidate]:
    final: dict[str, Candidate] = {}
    for event in events:
        if isinstance(event, CandidateEvent):
            final[event.candidate.id] = event.candidate
    return final


def final_winner(events: list[EventBase]) -> str | None:
    winner: str | None = None
    for event in events:
        if isinstance(event, WinnerEvent):
            winner = event.candidate_id
    return winner


def pack_unchanged(pack: TaskPack) -> bool:
    try:
        pack.reverify()
        return verify_manifest(pack.root) == pack.sha256
    except Exception:
        return False


async def run_pipeline(env: EvalEnv, pack: TaskPack, *, priority: str | None = None, mode: str = "full") -> PipelineRun:
    """One full orchestrator run with the env's provider and tools. Never raises for a failed run: `error` is set."""
    chosen = priority or pack.default_priority
    run_id = "r_eval_" + uuid.uuid4().hex[:16]
    limits = env.limits_for(pack)
    ledger = RunLedger(limits, env.settings)
    llm = RunLLM(env.provider, env.settings, ledger)
    hub = EventHub(env.store, env.settings)
    ctx = RunContext(
        run_id=run_id,
        session_id="eval",
        pack=pack,
        priority=chosen,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        settings=env.settings,
        llm=llm,
        roles=default_roles(),
        tools=env.tools,
        ledger=ledger,
        sink=hub,
        store=env.store,
    )
    now = datetime.now(UTC)
    await env.store.create_run(
        RunRecord(
            id=run_id,
            session_id="eval",
            preset_id=pack.id,
            priority=chosen,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            status="running",
            created_at=now,
            started_at=now,
            budget=limits,
            task_pack_sha256=pack.sha256,
            tool_image_digest=env.tool_image_digest,
            models=env.settings.models_by_role(),
            git_sha=env.git_sha,
        )
    )
    hub.open_run(run_id, started_monotonic=ctx.started_monotonic, ledger=ledger, ctx=ctx)

    error: str | None = None
    started = time.monotonic()
    try:
        await run_orchestrator(ctx)
    except Exception as exc:  # the orchestrator reports expected failures itself; this is a crash
        log.exception("eval run %s crashed", run_id)
        error = f"{type(exc).__name__}: {exc}"
    wall_s = time.monotonic() - started

    events = await env.store.events_after(run_id, 0)
    hub.close_run(run_id)
    record = await env.store.get_run(run_id)
    candidates = last_candidates(events)
    outcome = ctx.outcome
    if error is None and outcome is not None and outcome.status == "error":
        error = outcome.error_reason or "the run ended with an error"
    return PipelineRun(
        run_id=run_id,
        events=events,
        candidates=candidates,
        winner_id=final_winner(events),
        outcome=outcome,
        spend=ledger.snapshot(),
        wall_s=wall_s,
        stage_times=stage_times_from(events),
        prompt_versions=dict(record.prompt_versions) if record else {},
        pack_hash_ok=pack_unchanged(pack),
        error=error,
        llm_calls=list(llm.calls),
    )


async def cached_pipeline(env: EvalEnv, pack: TaskPack, repeat: int, mode: str = "full") -> PipelineRun:
    """Pipeline run shared by the optimization, regression and performance suites within one eval invocation."""
    key = (pack.id, repeat, mode)
    cached = env.pipeline_cache.get(key)
    if cached is None:
        cached = await run_pipeline(env, pack, mode=mode)
        env.pipeline_cache[key] = cached
    return cached


def fill_from_pipeline(record: EvalRecord, run: PipelineRun) -> EvalRecord:
    """Copy the 14.4 metrics out of a pipeline run into `record`."""
    record.candidate_count = len(run.candidates)
    record.llm_calls = run.spend.llm_calls
    record.input_tokens = run.spend.input_tokens
    record.output_tokens = run.spend.output_tokens
    record.reasoning_tokens = sum(int(call.get("reasoningTokens", 0)) for call in run.llm_calls)
    reasons: dict[str, int] = defaultdict(int)
    for call in run.llm_calls:
        reasons[str(call.get("finishReason") or "unknown")] += 1
    record.finish_reasons = dict(reasons)
    record.est_usd = round(run.spend.usd, 6)
    record.runtime_s = round(run.wall_s, 2)
    record.stage_times = run.stage_times
    record.prompt_versions = run.prompt_versions
    record.pack_hash_ok = run.pack_hash_ok
    record.error = run.error

    base = run.baseline
    if base is not None:
        first = base.attempts[0].verification if base.attempts else None
        record.initial_pass = bool(first is not None and first.lint == "pass" and first.passed == first.total > 0)
        record.final_pass = base.status in ("pass", "measured")
        record.repairs_used = base.fix_count
        record.repair_success = (base.fix_count > 0 and record.final_pass) if base.fix_count else None
        if base.ppa is not None:
            record.baseline_area_um2 = base.ppa.area_um2
            record.baseline_clock_ns = base.ppa.clock_period_ns
    win = run.winner
    if win is not None and win.ppa is not None:
        record.final_area_um2 = win.ppa.area_um2
        record.final_clock_ns = win.ppa.clock_period_ns
    measured = [c for c in run.candidates.values() if c.fitness is not None and c.status == "measured"]
    if measured:
        record.fitness_best = max(c.fitness.score for c in measured if c.fitness is not None)
    record.improved = bool(win is not None and win.id != "c1" and win.fitness is not None and win.fitness.score > 0)
    if win is not None and win.eqy is not None:
        record.equivalence = win.eqy.status
    elif win is not None and win.id == "c1":
        record.equivalence = "not_run"
    return record
