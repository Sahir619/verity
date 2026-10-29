"""Serial real reference measurements. Run from backend: python tests/bench/measure_references.py."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verity.config import AGENT_ROOT, load_settings
from verity.core.pack import TaskPack
from verity.tools.bench import parse_bench
from verity.tools.local_docker import LocalDockerRunner


def print_table(results):
    print("| Core | Benchmark | Wait | Cycles | Instructions | CPI | Busy | Fetch | Data | Pass |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for result in results:
        for row in result["bench"]:
            stall = row["cyclesByStall"]
            print(f"| {result['top']} | {row['name']} | {result['wait_cycles']} | {row['cycles']} | "
                  f"{row['instructions']} | {row['cpi']:.6f} | {stall['busy']} | {stall['fetch']} | "
                  f"{stall['data']} | {row['passed']} |")


async def main():
    settings = load_settings(provider="fake")
    settings.tools.image = os.environ.get("VERITY_BENCH_IMAGE", "verity-tools:w13")
    runner = LocalDockerRunner(settings)
    results = []
    for pack_id in ("rv32i_core", "rv32i_cache_core"):
        pack = TaskPack.load(AGENT_ROOT / "tasks" / pack_id)
        rtl = pack.validate_rtl(pack.reference_rtl())
        for wait in (0, 3):
            result = await runner._run(
                "bench", pack, {"dut.v": rtl.data}, 300,
                lambda data: parse_bench(data, expected_names=("crc32", "matmult-int")),
                lambda raw: parse_bench({"status": "timeout", "wait_cycles": wait}),
                extra_env={"VERITY_BENCH_NAMES": "crc32,matmult-int", "VERITY_BENCH_WAIT": str(wait),
                           "VERITY_BENCH_TIMEOUT_S": "300"},
            )
            record = {"top": pack_id, **result.model_dump(by_alias=True)}
            results.append(record)
            print(json.dumps(record), flush=True)
            if result.status != "pass":
                raise RuntimeError(f"measurement failed: {pack_id}, wait {wait}")
    path = Path(tempfile.gettempdir()) / "verity-w13-measurements.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"raw measurements: {path}")
    print_table(results)


if __name__ == "__main__":
    asyncio.run(main())
