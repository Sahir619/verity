"""CLI: python -m verity.evals --suite <name> --tasks all --provider fake|nebius --repeats N

Suites (ARCHITECTURE 14.3): packs, functional, repair, optimization, regression, performance, isolation, or `all`.
Every run writes one JSON line per task per repeat to evals/results/<eval_run_id>.jsonl and regenerates
evals/report.md from every result file in that folder.

Examples:
  python -m verity.evals --suite isolation --tasks all --provider fake            # $0, no LLM
  python -m verity.evals --suite packs --tasks all --provider fake                # $0, needs the sandbox image
  python -m verity.evals --suite functional --tasks add8,cnt8 --provider nebius --repeats 3
  python -m verity.evals --report-only                                            # rebuild report.md only

Exit code 1 when a self-check suite (packs, isolation, regression) has a failing row, 2 for a usage error.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from ..config import AGENT_ROOT
from ..core.pack import TaskPack
from . import suite_functional, suite_isolation, suite_packs, suite_pipeline, suite_repair
from .harness import DEFAULT_RESULTS_DIR, EvalEnv, open_env, select_packs
from .record import load_records
from .report import write_report

log = logging.getLogger("verity.evals")

SuiteFn = Callable[[EvalEnv, list[TaskPack], int], Awaitable[None]]

SUITE_ORDER: tuple[str, ...] = (
    "isolation",
    "packs",
    "functional",
    "repair",
    "optimization",
    "regression",
    "performance",
)
SELF_CHECK_SUITES = frozenset({"packs", "isolation", "regression"})
DEFAULT_REPORT = AGENT_ROOT / "evals" / "report.md"


def _suite_fn(name: str, mode: str) -> SuiteFn:
    async def optimization(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
        await suite_pipeline.run_optimization(env, packs, repeats, mode)

    async def regression(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
        await suite_pipeline.run_regression(env, packs, repeats, mode)

    async def performance(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
        await suite_pipeline.run_performance(env, packs, repeats, mode)

    table: dict[str, SuiteFn] = {
        "packs": suite_packs.run,
        "functional": suite_functional.run,
        "repair": suite_repair.run,
        "optimization": optimization,
        "regression": regression,
        "performance": performance,
        "isolation": suite_isolation.run,
    }
    return table[name]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m verity.evals", description="Verity evals (ARCHITECTURE 14).")
    parser.add_argument("--suite", choices=[*SUITE_ORDER, "all"], help="which suite to run")
    parser.add_argument("--tasks", default="all", help="`all` or a comma separated list of task ids (default all)")
    parser.add_argument("--provider", choices=["fake", "nebius"], default="fake", help="fake costs $0, nebius spends credits")
    parser.add_argument("--repeats", type=int, default=1, help="repeats per task (default 1)")
    parser.add_argument("--mode", choices=["fast", "full"], default="full", help="path for the pipeline suites (default full)")
    parser.add_argument("--skip-sandbox", action="store_true", help="skip every step that needs the tool image")
    parser.add_argument("--max-total-usd", type=float, default=3.0, help="stop starting work after this much estimated spend")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--report-only", action="store_true", help="rebuild report.md from existing result files and exit")
    return parser


async def _run(args: argparse.Namespace) -> int:
    suites = list(SUITE_ORDER) if args.suite == "all" else [args.suite]
    if args.provider == "nebius":
        from ..config import load_settings

        key = load_settings().llm.api_key
        if key is None or not key.get_secret_value():
            print("error: --provider nebius needs LLM_API_KEY (and LLM_BASE_URL) in the environment or backend/.env", file=sys.stderr)
            return 2

    # The functional suite measures pass@1 of the pipeline: a fake provider must not inject its deliberate bug there.
    env = await open_env(
        args.provider,
        results_dir=args.results_dir,
        inject_bug=suites != ["functional"],
        skip_sandbox=args.skip_sandbox,
        max_total_usd=args.max_total_usd,
    )
    failed = False
    try:
        packs = select_packs(env, args.tasks)
        if not packs:
            print("error: no task packs selected", file=sys.stderr)
            return 2
        print(f"eval run {env.eval_run_id}: provider={args.provider} suites={','.join(suites)} tasks={','.join(p.id for p in packs)}")
        for name in suites:
            print(f"-- suite {name}")
            await _suite_fn(name, args.mode)(env, packs, max(1, args.repeats))
        for row in load_records(env.results.path) if env.results.path.exists() else []:
            if row.suite in SELF_CHECK_SUITES and row.ok is False:
                failed = True
                print(f"FAIL {row.suite}/{row.task_id}: {row.error or row.note or 'see the result file'}")
        print(f"results: {env.results.path}  (estimated spend ${env.total_usd:.4f})")
    finally:
        await env.aclose()
    out = write_report(args.results_dir, args.report_out)
    print(f"report: {out}")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.report_only:
        out = write_report(args.results_dir, args.report_out)
        print(f"report: {out}")
        raise SystemExit(0)
    if args.suite is None:
        build_parser().print_usage(sys.stderr)
        print("error: --suite is required (or --report-only)", file=sys.stderr)
        raise SystemExit(2)
    if args.repeats < 1:
        print("error: --repeats must be at least 1", file=sys.stderr)
        raise SystemExit(2)
    try:
        code = asyncio.run(_run(args))
    except ValueError as exc:  # unknown task id
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
