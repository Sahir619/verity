"""Run one CPU Lab job end to end outside HTTP and print its progress.

Uses a fresh data folder under evals/work/ so the live database is never touched. The tool runner is the real
Docker sandbox. Spend is real only with --provider nebius (and real search only with --research real).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe evals/cpu_job_probe.py --provider fake --research fake
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from verity.config import load_settings  # noqa: E402
from verity.contracts import to_wire  # noqa: E402
from verity.contracts.cpu import CreateCpuJobRequest, UpgradeRequest  # noqa: E402
from verity.core.pack import load_packs  # noqa: E402
from verity.core.parse import live_families  # noqa: E402
from verity.llm.factory import build_provider  # noqa: E402
from verity.llm.fake import FakeProvider  # noqa: E402
from verity.research import FakeSearchAdapter, build_search_adapter  # noqa: E402
from verity.research.service import ResearchService  # noqa: E402
from verity.runs.manager import RunManager  # noqa: E402
from verity.store import SqliteStore  # noqa: E402
from verity.tools.factory import build_tool_runner  # noqa: E402

DONE = {"passed", "failed", "stopped"}


def _line(view: dict) -> str:
    blocks = " ".join(f"{b['blockId'].removeprefix('rv32i_')}:{b['status']}" for b in view["blocks"])
    core = view["core"]["status"]
    return f"{view['status']:<12} ${view.get('spentUsd', 0):.3f}  {blocks}  core:{core}"


def _keep_awake() -> None:
    """Windows only: ask the OS not to idle-sleep while this process runs (released when it exits). Docker stalls
    when a laptop enters standby, and a stalled sandbox fails a CPU job. Closing the lid can still force sleep."""
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


async def main(args: argparse.Namespace) -> int:
    _keep_awake()
    base = load_settings(provider=args.provider)
    work = (ROOT / "evals" / "work" / f"cpu_{time.strftime('%Y%m%dT%H%M%S')}").resolve()
    plan = base.plan.model_copy(update={"gate_auto_continue_ms": 0, "gate_after_replan_ms": 0})
    update: dict[str, object] = {"data_dir": work, "plan": plan}
    if args.width is not None:  # fewer sandbox runs at once on a machine short of memory
        update["cpu_job"] = base.cpu_job.model_copy(update={"width": args.width})
        update["limits"] = base.limits.model_copy(update={"concurrent_global": args.width})
    settings = base.model_copy(update=update)
    provider = FakeProvider(settings, delay_s=0.0, inject_bug=args.inject_bug) if args.provider == "fake" else build_provider(settings)
    if args.containers is not None:  # probe-only cap on sandbox containers at once (each may use 2 GB)
        import verity.tools.local_docker as local_docker

        local_docker.MAX_PARALLEL_CONTAINERS = args.containers
    tools = build_tool_runner(settings)
    store = SqliteStore(settings)
    await store.open()
    packs = load_packs(settings.tasks_dir)
    adapter = None if args.research == "off" else FakeSearchAdapter() if args.research == "fake" else build_search_adapter(settings)
    research = ResearchService(settings, store, provider, adapter, live_families(packs)) if adapter is not None else None
    runs = RunManager(settings, store, provider, tools, packs, research=research, threads=store)
    await runs.start()
    print(f"data: {work}\nprovider: {args.provider}  research: {args.research}  goal: {args.goal}", flush=True)
    started = time.monotonic()
    try:
        async def follow(job_id: str, name: str) -> dict:
            last = ""
            while True:
                view = await runs.cpu_jobs.get(job_id, session_id="cpu_probe", role="super_admin")
                wire = to_wire(view)
                line = _line(wire)
                if line != last:
                    print(f"[{time.monotonic() - started:7.0f}s] {line}", flush=True)
                    last = line
                if wire["status"] in DONE:
                    break
                await asyncio.sleep(5)
            out = work / f"{name}_view.json"
            out.write_text(json.dumps(wire, indent=2), encoding="utf-8")
            print(f"final {name}: {wire['status']}  message: {wire.get('message')}  view: {out}", flush=True)
            return wire

        view = await runs.cpu_jobs.create(
            CreateCpuJobRequest(spec_id="rv32i_micro", goal=args.goal), session_id="cpu_probe", role="super_admin"
        )
        wire = await follow(view.id, "cpu_job")
        ok = wire["status"] == "passed"
        for upgrade_id in [u for u in (args.upgrade or "").split(",") if u] if ok else []:
            child = await runs.cpu_jobs.upgrade(
                view.id, UpgradeRequest(upgrade_id=upgrade_id), session_id="cpu_probe", role="super_admin"
            )
            print(f"upgrade {upgrade_id}: child {child.id}", flush=True)
            ok = (await follow(child.id, upgrade_id))["status"] == "passed" and ok
        return 0 if ok else 1
    finally:
        await runs.shutdown()
        await store.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["fake", "nebius"], default="fake")
    p.add_argument("--research", choices=["off", "fake", "real"], default="fake")
    p.add_argument("--goal", choices=["area", "speed", "power"], default="area")
    p.add_argument("--width", type=int, default=None, help="block runs at once (also caps the global run slots)")
    p.add_argument("--containers", type=int, default=None, help="sandbox containers at once (default 4)")
    p.add_argument("--inject-bug", action="store_true", help="fake provider only: plant a bug so the repair path runs")
    p.add_argument("--upgrade", default="", help="after the job passes, run these upgrades in turn, e.g. pipeline_2,cache_16")
    sys.exit(asyncio.run(main(p.parse_args())))
