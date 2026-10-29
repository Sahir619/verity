"""Simulation helpers shared by /verity/bin/seeds, /verity/bin/strength and /verity/bin/wave. Standard library only.

The flow is the same one /verity/bin/verify uses (iverilog -g2012, then vvp with the cocotb VPI module and the
locked test_dut.py from the pack): the same environment names, the same timescale file, the same result file
(schema verity.sim/1, written by verity_tb.Recorder). verify keeps its own copy so its output never changes.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import verity_common as vc

LIB_DIR = Path(__file__).resolve().parent
TIMESCALE_FILE = "`timescale 1ns/1ps\n"
LINT_CMD = ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "-Wno-DECLFILENAME"]
_WARNING_TAG = re.compile(r"^%Warning-([A-Za-z0-9_]+)", re.MULTILINE)
_cocotb_paths: tuple[str, str] | None = None


# ---------- lint ----------
def lint_counts(text: str) -> dict[str, Any]:
    """Errors, warnings, warnings per Verilator tag, and the LATCH count, from a Verilator log."""
    errors = len(re.findall(r"^%Error", text, re.MULTILINE))
    tags = _WARNING_TAG.findall(text)
    by_tag: dict[str, int] = {}
    for tag in tags:
        by_tag[tag] = by_tag.get(tag, 0) + 1
    return {"errors": errors, "warnings": len(tags), "by_tag": by_tag, "latches": by_tag.get("LATCH", 0)}


def run_lint(top: str, dut: Path, versions: dict[str, str], log_cap: int = 4_000) -> tuple[dict[str, Any] | None, str]:
    """Verilator lint exactly as verify runs it. Returns (lint dict, "") or (None, "timeout")."""
    timeout = vc.env_float("VERITY_LINT_TIMEOUT_S", 20)
    proc = vc.run([*LINT_CMD, "--top-module", top, str(dut)], timeout_s=timeout)
    if proc.timed_out:
        return None, "timeout"
    text = vc.scrub_paths(proc.output)
    counts = lint_counts(text)
    return {
        "ok": proc.rc == 0 and counts["errors"] == 0,
        **counts,
        "log": vc.head(text, log_cap),
        "tool": f"Verilator {versions.get('verilator', 'unknown')}",
    }, ""


# ---------- compile ----------
def compile_design(
    top: str, sources: list[Path], build: Path, *, timeout_s: float, extra_tops: tuple[str, ...] = ()
) -> tuple[Path | None, str]:
    """iverilog -g2012 into build/sim.vvp. Returns (vvp path or None, tool output with paths scrubbed)."""
    vc.fresh_dir(build)
    (build / "timescale.v").write_text(TIMESCALE_FILE, encoding="ascii")
    vvp = build / "sim.vvp"
    tops: list[str] = []
    for name in (top, *extra_tops):
        tops += ["-s", name]
    proc = vc.run(
        ["iverilog", "-g2012", "-DCOCOTB_SIM=1", *tops, "-o", str(vvp), str(build / "timescale.v"), *map(str, sources)],
        timeout_s=max(1.0, timeout_s),
    )
    text = vc.scrub_paths(proc.output)
    for src in sources:
        text = text.replace(str(src), src.name)
    if proc.timed_out:
        return None, "compile timed out"
    if proc.rc != 0 or not vvp.is_file():
        return None, text or "iverilog failed to compile the design"
    return vvp, text


# ---------- run the locked tests ----------
@dataclass
class SimRun:
    data: dict[str, Any] | None  # the verity.sim/1 result file, or None when the testbench wrote none
    timed_out: bool
    log: str


def _cocotb() -> tuple[str, str]:
    global _cocotb_paths
    if _cocotb_paths is None:
        lib_dir = vc.run(["cocotb-config", "--lib-dir"], timeout_s=20).output.strip().splitlines()[-1]
        libpython = vc.run(["cocotb-config", "--libpython"], timeout_s=20).output.strip().splitlines()[-1]
        _cocotb_paths = (lib_dir, libpython)
    return _cocotb_paths


def run_tests(
    task: dict[str, Any],
    vvp: Path,
    run_dir: Path,
    *,
    seed: int,
    timeout_s: float,
    extra_env: dict[str, str] | None = None,
) -> SimRun:
    """One vvp run of the pack's locked cocotb test with the given seed. Never raises for a failing design."""
    top = task["top"]
    sim = task.get("sim", {})
    vc.fresh_dir(run_dir)
    result_file = run_dir / "sim_result.json"
    lib_dir, libpython = _cocotb()
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join([str(vc.pack_dir()), str(LIB_DIR), *sys.path]),
            "PYTHONHOME": sys.base_prefix,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "LIBPYTHON_LOC": libpython,
            "MODULE": "test_dut",
            "COCOTB_TEST_MODULES": "test_dut",
            "TOPLEVEL": top,
            "COCOTB_TOPLEVEL": top,
            "TOPLEVEL_LANG": "verilog",
            "COCOTB_RESULTS_FILE": str(run_dir / "results.xml"),
            "COCOTB_RANDOM_SEED": str(seed),
            "COCOTB_REDUCED_LOG_FMT": "1",
            "COCOTB_ANSI_OUTPUT": "0",
            "VERITY_SIM_RESULT": str(result_file),
            "VERITY_SEED": str(seed),
            "VERITY_MAX_CYCLES": str(int(sim.get("max_cycles_per_case", 1000))),
            "VERITY_TEST_COUNT": str(int(task["test_count"])),
            **(extra_env or {}),
        }
    )
    proc = vc.run(
        ["vvp", "-M", lib_dir, "-m", "libcocotbvpi_icarus", str(vvp)],
        timeout_s=max(1.0, timeout_s),
        cwd=run_dir,
        env=env,
    )
    data: dict[str, Any] | None = None
    if result_file.is_file():
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    return SimRun(data=data, timed_out=proc.timed_out, log=vc.scrub_paths(proc.output))


def full_pass(task: dict[str, Any], data: dict[str, Any] | None) -> bool:
    """The same pass rule as verify: the full case count ran and every case passed."""
    if data is None:
        return False
    expected = int(task["test_count"])
    total = int(data.get("total", 0))
    passed = int(data.get("passed", 0))
    failures = int(data.get("failure_count", total - passed))
    return total == expected and passed == expected and failures == 0
