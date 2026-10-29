"""Real sandbox coverage. W8 tests intentionally require the newly built image."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from verity.core.rtl import Rtl
from verity.tools.bench import BENCH_NAMES, parse_bench
from verity.tools.local_docker import LocalDockerRunner

FIXTURE = Path(__file__).parent / "fixtures/rv32i_core.v"


def _docker_up() -> bool:
    """True when the Docker daemon answers. Only the daemon gates the file: a missing image still fails."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _docker_up(), reason="Docker is not running")


def runner_and_rtl(settings):
    settings.tools.image = os.environ.get("VERITY_BENCH_IMAGE", "verity-tools:0.2")
    return LocalDockerRunner(settings), Rtl.validate(FIXTURE.read_text(), top="rv32i_core", max_lines=2000)


async def run_one(runner, rtl, pack, wait_cycles=0):
    return await runner._run(
        "bench", pack, {"dut.v": rtl.data}, 300,
        lambda data: parse_bench(data, expected_names=("crc32",)),
        lambda raw: parse_bench({"status": "timeout"}),
        extra_env={"VERITY_BENCH_NAMES": "crc32", "VERITY_BENCH_TIMEOUT_S": "300",
                   "VERITY_BENCH_WAIT": str(wait_cycles)},
    )


async def test_one_real_benchmark(settings, packs):
    runner, rtl = runner_and_rtl(settings)
    result = await run_one(runner, rtl, packs["rv32i_core"])
    assert result.status == "pass", result
    row, = result.bench
    assert row.name == "crc32" and row.cycles > row.instructions > 1000
    assert row.self_check == 1 and row.passed and row.cpi == row.cycles / row.instructions
    assert row.cycles_by_stall.busy + row.cycles_by_stall.fetch + row.cycles_by_stall.data == row.cycles


async def test_real_suite(settings, packs):
    runner, rtl = runner_and_rtl(settings)
    result = await runner.bench(rtl, packs["rv32i_core"])
    print("\nREAL_BENCH_RESULT=" + json.dumps(result.model_dump(by_alias=True), separators=(",", ":")))
    assert result.status == "pass", result
    assert [row.name for row in result.bench] == list(BENCH_NAMES)
    assert result.duration_ms < 300000


async def test_real_failed_self_check_is_not_a_pass(settings, packs):
    runner, rtl = runner_and_rtl(settings)
    text = rtl.text.replace("mem_wdata = store_data;", "mem_wdata = mem_addr == 32'hfffffff8 ? 0 : store_data;")
    bad = Rtl.validate(text, top="rv32i_core", max_lines=2000)
    result = await run_one(runner, bad, packs["rv32i_core"])
    assert result.status == "fail" and result.bench[0].self_check == 0 and not result.bench[0].passed


async def test_real_wait_three_costs_more_cycles(settings, packs):
    runner, rtl = runner_and_rtl(settings)
    fast = await run_one(runner, rtl, packs["rv32i_core"], wait_cycles=0)
    flash = await run_one(runner, rtl, packs["rv32i_core"], wait_cycles=3)
    assert fast.status == flash.status == "pass", (fast, flash)
    assert fast.wait_cycles == 0 and flash.wait_cycles == 3
    a, b = fast.bench[0], flash.bench[0]
    assert b.instructions == a.instructions
    assert b.cycles > a.cycles and b.cycles_by_stall.fetch > a.cycles_by_stall.fetch
    assert b.cycles_by_stall.data > a.cycles_by_stall.data


async def test_cached_programs_report_hits(settings, packs):
    runner, _ = runner_and_rtl(settings)
    pack = packs["rv32i_cache_core"]
    result = await runner.verify(pack.validate_rtl(pack.reference_rtl()), pack)
    assert result.passed_all(pack.test_count), result
    hits = [line for line in result.sim.log.splitlines() if "CACHE_HITS" in line]
    print("\n" + "\n".join(hits))
    assert len(hits) == 3, result.sim.log
