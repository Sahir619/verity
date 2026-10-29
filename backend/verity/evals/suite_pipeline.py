"""`optimization`, `regression` and `performance` suites: one full orchestrator run per task and repeat.

optimization  best fitness vs v1, area and timing gains, improved or not, equivalence of the winner
regression    winner vs v1: EQY re-run and locked tests re-run on the winner, pack hash unchanged ($0 after the run)
performance   calls, tokens, estimated dollars, wall time and time per stage, taken from the run record

Within one `python -m verity.evals` invocation the three suites share the pipeline run of a (task, repeat), so
`--suite all` pays for each run once. Gains are always relative to our own v1, never to any published number.
"""

import logging

from ..core.errors import ToolInfraError
from ..core.pack import TaskPack
from .harness import EvalEnv, PipelineRun, cached_pipeline, fill_from_pipeline
from .record import EvalRecord

log = logging.getLogger("verity.evals")


async def run_optimization(env: EvalEnv, packs: list[TaskPack], repeats: int, mode: str = "full") -> None:
    await _loop(env, packs, repeats, "optimization", mode, _optimization_record)


async def run_performance(env: EvalEnv, packs: list[TaskPack], repeats: int, mode: str = "full") -> None:
    await _loop(env, packs, repeats, "performance", mode, _performance_record)


async def run_regression(env: EvalEnv, packs: list[TaskPack], repeats: int, mode: str = "full") -> None:
    await _loop(env, packs, repeats, "regression", mode, _regression_record)


async def _loop(env: EvalEnv, packs: list[TaskPack], repeats: int, suite: str, mode: str, build) -> None:
    if env.skip_sandbox:
        log.warning("the %s suite needs the sandbox: skipped (--skip-sandbox)", suite)
        return
    for pack in packs:
        for repeat in range(1, repeats + 1):
            if not env.budget_left():
                log.warning("eval budget reached, stopping the %s suite", suite)
                return
            run = await cached_pipeline(env, pack, repeat, mode)
            record = env.new_record(suite, pack.id, repeat)
            record.extra["mode"] = mode
            record.extra["run_id"] = run.run_id
            record.extra["outcome"] = run.outcome.status if run.outcome else None
            record = await build(env, pack, run, record)
            await env.write(record)


async def _optimization_record(env: EvalEnv, pack: TaskPack, run: PipelineRun, record: EvalRecord) -> EvalRecord:
    del env, pack
    fill_from_pipeline(record, run)
    base, win = run.baseline, run.winner
    if base and base.ppa and win and win.ppa:
        record.extra["area_gain_pct"] = _gain(base.ppa.area_um2, win.ppa.area_um2)
        record.extra["timing_gain_pct"] = _gain(base.ppa.clock_period_ns, win.ppa.clock_period_ns)
    record.extra["winner_id"] = run.winner_id
    return record


async def _performance_record(env: EvalEnv, pack: TaskPack, run: PipelineRun, record: EvalRecord) -> EvalRecord:
    del env, pack
    fill_from_pipeline(record, run)
    record.extra["spend"] = run.spend.model_dump(mode="json")
    return record


async def _regression_record(env: EvalEnv, pack: TaskPack, run: PipelineRun, record: EvalRecord) -> EvalRecord:
    """Re-prove the winner against v1 and re-run the locked tests on it, from scratch, in new sandboxes."""
    fill_from_pipeline(record, run)
    base, win = run.baseline, run.winner
    if base is None or win is None or base.status not in ("pass", "measured"):
        record.ok = False
        record.note = "no baseline or no winner to check"
        return record
    try:
        base_rtl = pack.validate_rtl(base.rtl, max_bytes=env.settings.rtl.max_bytes)
        win_rtl = pack.validate_rtl(win.rtl, max_bytes=env.settings.rtl.max_bytes)
        tests = await env.tools.verify(win_rtl, pack)
        record.extra["winner_tests_pass"] = tests.passed_all(pack.test_count)
        if win.id != base.id and pack.eqy.enabled:
            eq = await env.tools.check_equivalence(base_rtl, win_rtl, pack)
            record.equivalence = eq.to_equivalence(base.id).status
            record.extra["eqy_rerun"] = eq.status
        record.ok = bool(record.extra["winner_tests_pass"]) and record.equivalence != "not_equivalent"
    except ToolInfraError as exc:
        record.ok = False
        record.error = f"sandbox: {exc}"
    except Exception as exc:  # RtlRejected on a stored attempt would be a bug worth a row, not a crash
        record.ok = False
        record.error = f"{type(exc).__name__}: {exc}"
    return record


def _gain(base: float | None, value: float | None) -> float | None:
    if base is None or value is None or base <= 0:
        return None
    return round(100.0 * (base - value) / base, 2)
