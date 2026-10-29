"""Helpers shared by the /verity/bin stage scripts (verify, measure, eqy, bundle). Standard library only.

The stdout contract (CONTRACTS S4): everything human-readable goes to stderr; the LAST line of stdout is one
JSON object. Exit codes (CONTRACTS 3.4): 0 result printed, 2 input error, 3 internal error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_INTERNAL = 3
STDOUT_LIMIT = 60_000  # sandbox stdout cap is 64 KB (ARCHITECTURE 12.4)
DEFAULT_LIBERTY = "/verity/pdk/nangate45/NangateOpenCellLibrary_typical.lib"
VERSIONS_FILE = Path(os.environ.get("VERITY_VERSIONS_FILE", "/verity/versions.json"))


class InputError(Exception):
    """Bad or missing input (dut.v, task.toml). Exit code 2, no retry."""


class ToolMissing(Exception):
    """A tool binary is not installed or not executable. Exit code 3 (image problem)."""


# ---------- paths and environment ----------
def pack_dir() -> Path:
    return Path(os.environ.get("VERITY_PACK_DIR", "/pack"))


def work_dir() -> Path:
    return Path(os.environ.get("VERITY_WORK_DIR", "/work"))


def scratch_dir() -> Path:
    path = Path(os.environ.get("VERITY_TMP_DIR", "/tmp/verity"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def out_dir() -> Path:
    path = work_dir() / "out"
    path.mkdir(parents=True, exist_ok=True)
    return path


def liberty_path() -> Path:
    return Path(os.environ.get("VERITY_LIBERTY", DEFAULT_LIBERTY))


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- inputs ----------
def load_task() -> dict[str, Any]:
    path = pack_dir() / "task.toml"
    try:
        with path.open("rb") as fh:
            task = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    for key in ("top", "test_count", "ports"):
        if key not in task:
            raise InputError(f"{path}: missing key '{key}'")
    return task


def require_file(path: Path) -> Path:
    if not path.is_file():
        raise InputError(f"missing input file {path}")
    return path


# ---------- running tools ----------
@dataclass
class Proc:
    rc: int
    output: str  # stdout and stderr, interleaved
    timed_out: bool
    seconds: float


def run(
    cmd: list[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> Proc:
    """Run one tool with a hard deadline. The whole process group is killed on timeout. Never uses a shell."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603  (fixed argument lists, never model text)
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise ToolMissing(f"cannot start {cmd[0]}: {exc}") from exc
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=max(1.0, timeout_s))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        output, _ = proc.communicate()
    rc = 124 if timed_out else proc.returncode
    return Proc(rc=rc, output=(output or "")[-400_000:], timed_out=timed_out, seconds=time.monotonic() - started)


def head(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def tail(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else "[truncated]\n" + text[-limit:]


def first_line_matching(text: str, pattern: str, default: str = "") -> str:
    """First line that matches `pattern` (case-insensitive), else the first non-empty line, else default."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines:
        if re.search(pattern, line, re.IGNORECASE):
            return line[:300]
    return lines[0][:300] if lines else default


def scrub_paths(text: str) -> str:
    """Show the candidate as dut.v / b.v, not as a sandbox path (clearer repair prompts)."""
    wd = str(work_dir())
    return text.replace(f"{wd}/dut.v", "dut.v").replace(f"{wd}/b.v", "b.v")


# ---------- tool versions ----------
_PROBES: dict[str, tuple[list[str], str]] = {
    "riscv_gcc": (["riscv-none-elf-gcc", "--version"], r"\)\s+(\d+\.\d+\.\d+)"),
    "verilator": (["verilator", "--version"], r"Verilator\s+(\S+)"),
    "yosys": (["yosys", "-V"], r"Yosys\s+(\S+)"),
    "iverilog": (["iverilog", "-V"], r"version\s+(\S+)"),
    "opensta": (["sta", "-version"], r"(\d+\.\d+(?:\.\d+)?)"),
    "eqy": (["eqy", "--version"], r"(\S+)"),
}


def probe_versions() -> dict[str, str]:
    """Run every tool once and collect its version (used at image build time to write versions.json)."""
    found: dict[str, str] = {}
    for name, (cmd, pattern) in _PROBES.items():
        try:
            proc = run(cmd, timeout_s=20)
            match = re.search(pattern, proc.output)
            found[name] = match.group(1) if match else "unknown"
        except ToolMissing:
            found[name] = "missing"
    try:
        from importlib.metadata import version

        found["cocotb"] = version("cocotb")
    except Exception:  # noqa: BLE001
        found["cocotb"] = "missing"
    return found


def tool_versions(keys: tuple[str, ...]) -> dict[str, str]:
    """Versions for the given tools: from /verity/versions.json (written at build time), else probed live."""
    data: dict[str, str] = {}
    try:
        data = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = probe_versions()
    return {k: str(data.get(k, "unknown")) for k in keys}


# ---------- result output ----------
def _shrink(result: dict[str, Any], limit: int) -> None:
    """Halve every long 'log' string until the JSON fits."""
    for _ in range(8):
        if len(json.dumps(result, separators=(",", ":"))) <= limit:
            return
        stack: list[Any] = [result]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "log" and isinstance(value, str) and len(value) > 400:
                        node[key] = value[len(value) // 2 :]
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                stack.extend(v for v in node if isinstance(v, (dict, list)))


def emit(result: dict[str, Any], exit_code: int = EXIT_OK) -> NoReturn:
    """Write result.json and print the one-line JSON as the last line of stdout."""
    _shrink(result, STDOUT_LIMIT)
    line = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
    try:
        (out_dir() / "result.json").write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass
    sys.stdout.write("\n" + line + "\n")
    sys.stdout.flush()
    sys.exit(exit_code)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
