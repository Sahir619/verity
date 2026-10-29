"""Shared ToolRunner logic (CONTRACTS S13): one sandbox call per method, retry on infrastructure errors only.

Backends (LocalDockerRunner, NebiusSandboxRunner) implement `_execute`: run one fixed script with the given
files and return what it printed. Everything else lives here:

- the ToolRunner methods and their timeouts, environment and parsing (verify, measure, eqy, and the sign-off and
  waveform calls: seeds, strength, wave),
- error classification (CONTRACTS 3.4): exit 0 parse the JSON; exit 2 input error (no retry); exit 3 or any
  other code or unparseable output is an infrastructure error (retried `tools.infra_retries` times, then
  ToolInfraError); outer timeout becomes a "timeout" result and is never an exception,
- a measure cache keyed by (pack, rtl sha) so repeated measurement of the same RTL never re-runs the sandbox.

A failing design is never an exception: it is a result with a failing status.
"""

import asyncio
import json
import logging
import math
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from ..config import Settings
from ..core.errors import ToolInfraError
from ..core.mutants import Mutant
from ..core.pack import TaskPack
from ..core.results import (
    EquivalenceResult,
    GoldenCheckResult,
    MeasureResult,
    SeedsResult,
    StrengthResult,
    VerifyResult,
    WaveResult,
)
from ..core.rtl import Rtl
from .parse import SandboxOutputError, parse_stdout_json
from .bench import BENCH_TIMEOUT_S, BenchResult, parse_bench
from .parsers import parse_eqy, parse_golden_check, parse_measure, parse_seeds, parse_strength, parse_verify, parse_wave

log = logging.getLogger("verity.tools")

Script = Literal["verify", "measure", "eqy", "seeds", "strength", "wave", "check_golden", "bench"]
# Fixed command templates. Nothing derived from model output ever reaches a command line.
SCRIPT_PATHS: dict[Script, str] = {
    "verify": "/verity/bin/verify",
    "measure": "/verity/bin/measure",
    "eqy": "/verity/bin/eqy",
    "seeds": "/verity/bin/seeds",
    "strength": "/verity/bin/strength",
    "wave": "/verity/bin/wave",
    "check_golden": "/verity/bin/check_golden",
    "bench": "/verity/bin/bench",
}
SEED_WORKERS = 2  # /verity/bin/seeds runs two seeds at a time
COMPILE_MARGIN_S = 10
SANDBOX_PACK_DIR = "/pack"
SANDBOX_WORK_DIR = "/work"
RETRY_PAUSE_S = 1.0
MEASURE_CACHE_SIZE = 128
_CACHEABLE_MEASURE = frozenset({"ok", "synth_fail", "sta_fail"})

T = TypeVar("T", VerifyResult, MeasureResult, EquivalenceResult, SeedsResult, StrengthResult, WaveResult, BenchResult)


class FatalToolError(ToolInfraError):
    """An infrastructure error that retrying cannot fix (bad input, bad credentials, docker missing)."""


@dataclass(frozen=True, slots=True)
class SandboxCall:
    script: Script
    work_files: Mapping[str, bytes]  # file name under /work -> content
    pack: TaskPack | None  # None for check_golden: no pack exists yet at the self-check stage
    env: Mapping[str, str]
    timeout_s: int  # outer timeout: inner stage budgets plus sandbox_overhead_s


@dataclass(frozen=True, slots=True)
class RawOutput:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


def _tail(text: str, limit: int = 600) -> str:
    return text[-limit:] if len(text) > limit else text


class SandboxRunner(ABC):
    """ToolRunner base. Subclasses implement `_execute` and `image_digest`."""

    def __init__(self, settings: Settings, *, max_parallel: int) -> None:
        self._settings = settings
        self._retries = max(0, settings.tools.infra_retries)
        self._sem = asyncio.Semaphore(max_parallel)
        self._env = self._build_env(settings)
        self._measure_cache: OrderedDict[tuple[str, str, str], MeasureResult] = OrderedDict()
        self._measure_inflight: dict[tuple[str, str, str], asyncio.Task[MeasureResult]] = {}

    # ---------- backend hooks ----------
    @abstractmethod
    async def _execute(self, call: SandboxCall) -> RawOutput:
        """Run one script. Raise ToolInfraError (or FatalToolError) when the sandbox itself failed."""

    @abstractmethod
    async def image_digest(self) -> str: ...

    def config_problems(self) -> list[str]:
        """Human-readable reasons this backend cannot run (empty when ready). Used by health checks."""
        return []

    async def sandbox_problem(self) -> str | None:
        """A plain sentence when the sandbox cannot run anything right now (Docker down, image missing), else None.
        Must stay fast and cached: GET /health and POST /runs call it. Backends without a cheap check return None."""
        return None

    # ---------- environment ----------
    @staticmethod
    def _build_env(settings: Settings) -> dict[str, str]:
        t = settings.timeouts
        return {
            "VERITY_LINT_TIMEOUT_S": str(t.lint_s),
            "VERITY_SIM_TIMEOUT_S": str(t.sim_s),
            "VERITY_SYNTH_TIMEOUT_S": str(t.synth_s),
            "VERITY_STA_TIMEOUT_S": str(t.sta_s),
            "VERITY_EQY_TIMEOUT_S": str(t.eqy_s),
            "VERITY_PACK_DIR": SANDBOX_PACK_DIR,
            "VERITY_WORK_DIR": SANDBOX_WORK_DIR,
        }

    # ---------- ToolRunner ----------
    async def bench(self, rtl: Rtl, pack: TaskPack, wait_cycles: int = 0) -> BenchResult:
        """Run the image's locked Embench subset on one core, including pack blocks.v."""
        if type(wait_cycles) is not int or wait_cycles < 0:
            raise ValueError("wait_cycles must be a nonnegative integer")
        return await self._run(
            "bench", pack, {"dut.v": rtl.data}, BENCH_TIMEOUT_S, parse_bench,
            lambda raw: BenchResult(status="timeout", wait_cycles=wait_cycles, message="benchmark suite deadline exceeded", duration_ms=raw.duration_ms),
            extra_env={"VERITY_BENCH_TIMEOUT_S": str(BENCH_TIMEOUT_S), "VERITY_BENCH_WAIT": str(wait_cycles)},
        )

    async def verify(self, rtl: Rtl, pack: TaskPack) -> VerifyResult:
        inner = self._settings.timeouts.verify_s

        def on_timeout(raw: RawOutput) -> VerifyResult:
            return VerifyResult(
                status="timeout",
                message=f"verification did not finish within {inner} s",
                duration_ms=raw.duration_ms,
            )

        return await self._run(
            "verify",
            pack,
            {"dut.v": rtl.data},
            inner,
            lambda payload: parse_verify(payload, expected_total=pack.test_count),
            on_timeout,
        )

    async def measure(self, rtl: Rtl, pack: TaskPack) -> MeasureResult:
        key = (pack.id, pack.sha256, rtl.sha256)
        cached = self._measure_cache.get(key)
        if cached is not None:
            self._measure_cache.move_to_end(key)
            return cached.model_copy(deep=True)
        task = self._measure_inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._measure_uncached(rtl, pack))
            self._measure_inflight[key] = task
            task.add_done_callback(lambda done, k=key: self._measure_done(k, done))
        # shield: one caller being cancelled (Stop) must not cancel the call another caller is waiting on.
        result = await asyncio.shield(task)
        return result.model_copy(deep=True)

    def _measure_done(self, key: tuple[str, str, str], task: "asyncio.Task[MeasureResult]") -> None:
        self._measure_inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:  # exception() also marks it as retrieved
            return
        result = task.result()
        if result.status in _CACHEABLE_MEASURE:
            self._measure_cache[key] = result
            while len(self._measure_cache) > MEASURE_CACHE_SIZE:
                self._measure_cache.popitem(last=False)

    async def _measure_uncached(self, rtl: Rtl, pack: TaskPack) -> MeasureResult:
        inner = self._settings.timeouts.measure_s

        def on_timeout(raw: RawOutput) -> MeasureResult:
            return MeasureResult(
                status="timeout",
                message=f"synthesis and timing did not finish within {inner} s",
                duration_ms=raw.duration_ms,
            )

        return await self._run("measure", pack, {"dut.v": rtl.data}, inner, parse_measure, on_timeout)

    async def check_equivalence(self, gold: Rtl, gate: Rtl, pack: TaskPack) -> EquivalenceResult:
        inner = self._settings.timeouts.eqy_s

        def on_timeout(raw: RawOutput) -> EquivalenceResult:
            return EquivalenceResult(
                status="timeout",
                message=f"equivalence check did not finish within {inner} s",
                duration_ms=raw.duration_ms,
            )

        # v1 (gold) is /work/dut.v, the candidate (gate) is /work/b.v (CONTRACTS 3.7).
        return await self._run("eqy", pack, {"dut.v": gold.data, "b.v": gate.data}, inner, parse_eqy, on_timeout)

    # ---------- sign-off and waveform (CONTRACTS 3.9 to 3.11) ----------
    async def verify_seeds(self, rtl: Rtl, pack: TaskPack, seeds: Sequence[int]) -> SeedsResult:
        """The locked tests on more seeds in one call, plus lint warnings by tag. The pack files never change."""
        t = self._settings.timeouts
        inner = t.lint_s + t.sim_s * math.ceil(max(1, len(seeds)) / SEED_WORKERS) + COMPILE_MARGIN_S

        def on_timeout(raw: RawOutput) -> SeedsResult:
            return SeedsResult(status="timeout", message=f"the extra seeds did not finish within {inner} s", duration_ms=raw.duration_ms)

        return await self._run(
            "seeds",
            pack,
            {"dut.v": rtl.data},
            inner,
            lambda payload: parse_seeds(payload, expected_total=pack.test_count),
            on_timeout,
            extra_env={"VERITY_SEEDS": ",".join(str(int(s)) for s in seeds)},
        )

    async def test_strength(self, rtl: Rtl, pack: TaskPack, mutants: Sequence[Mutant]) -> StrengthResult:
        """The locked tests on every planted mutant in one call. `rtl` is the design the mutants came from."""
        cfg = self._settings.signoff
        inner = cfg.strength_budget_s + cfg.mutant_timeout_s + COMPILE_MARGIN_S
        body = json.dumps({"mutants": [{"id": m.id, "text": m.text} for m in mutants]}).encode("utf-8")

        def on_timeout(raw: RawOutput) -> StrengthResult:
            return StrengthResult(status="timeout", message=f"the planted bugs did not finish within {inner} s", duration_ms=raw.duration_ms)

        return await self._run(
            "strength",
            pack,
            {"dut.v": rtl.data, "mutants.json": body},
            inner,
            parse_strength,
            on_timeout,
            extra_env={
                "VERITY_STRENGTH_BUDGET_S": str(cfg.strength_budget_s),
                "VERITY_MUTANT_TIMEOUT_S": str(cfg.mutant_timeout_s),
            },
        )

    async def waveform(self, rtl: Rtl, pack: TaskPack, case: str | None = None) -> WaveResult:
        """One extra simulation with a waveform dump of one case (None: the first case), cut around its failure."""
        inner = self._settings.timeouts.sim_s + COMPILE_MARGIN_S

        def on_timeout(raw: RawOutput) -> WaveResult:
            return WaveResult(status="timeout", message=f"the waveform did not finish within {inner} s", duration_ms=raw.duration_ms)

        return await self._run(
            "wave",
            pack,
            {"dut.v": rtl.data},
            inner,
            parse_wave,
            on_timeout,
            extra_env={"VERITY_WAVE_CASE": case or ""},
        )

    # ---------- CE3: self-check of a drafted golden model, before any Verilog exists ----------
    async def check_golden(self, golden_py: str, cases: Sequence[dict]) -> GoldenCheckResult:
        inner = self._settings.timeouts.golden_check_s

        def on_timeout(raw: RawOutput) -> GoldenCheckResult:
            return GoldenCheckResult(status="timeout", message=f"the self-check did not finish within {inner} s", duration_ms=raw.duration_ms)

        body = json.dumps({"cases": list(cases)}).encode("utf-8")
        return await self._run(
            "check_golden",
            None,
            {"golden.py": golden_py.encode("utf-8"), "cases.json": body},
            inner,
            lambda payload: parse_golden_check(payload, expected_total=len(cases)),
            on_timeout,
        )

    # ---------- one sandbox call with retry ----------
    async def _run(
        self,
        script: Script,
        pack: TaskPack | None,
        work_files: Mapping[str, bytes],
        inner_s: int,
        parse: Callable[[dict[str, Any]], T],
        on_timeout: Callable[[RawOutput], T],
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> T:
        if pack is not None and (pack.root / "blocks.v").is_file():
            pack.reverify()
            blocks = b"\n\n" + (pack.root / "blocks.v").read_bytes()
            work_files = dict(work_files)
            for name in ("dut.v", "b.v"):
                if name in work_files:
                    work_files[name] += blocks
            # Strength replaces dut.v with each mutant's whole file.
            if "mutants.json" in work_files:
                body = json.loads(work_files["mutants.json"])
                for mutant in body["mutants"]:
                    mutant["text"] += blocks.decode("utf-8")
                work_files["mutants.json"] = json.dumps(body).encode("utf-8")
        call = SandboxCall(
            script=script,
            work_files=work_files,
            pack=pack,
            env={**self._env, **(extra_env or {})},
            timeout_s=inner_s + self._settings.timeouts.sandbox_overhead_s,
        )
        failure: ToolInfraError | None = None
        for attempt in range(self._retries + 1):
            if attempt:
                await asyncio.sleep(RETRY_PAUSE_S)
            try:
                async with self._sem:
                    raw = await self._execute(call)
            except FatalToolError:
                raise
            except ToolInfraError as exc:
                log.warning("sandbox %s attempt %d failed: %s", script, attempt + 1, exc)
                failure = exc
                continue

            log.info("sandbox %s exit=%s timed_out=%s %d ms", script, raw.exit_code, raw.timed_out, raw.duration_ms)
            if raw.timed_out or raw.exit_code == 124:
                return on_timeout(raw)

            if raw.exit_code == 2:
                raise FatalToolError(
                    f"{script}: input error: {self._message_of(raw)}",
                    script=script,
                    exit_code=2,
                    stderr_tail=_tail(raw.stderr),
                )

            if raw.exit_code == 0:
                try:
                    payload = parse_stdout_json(raw.stdout)
                    status = payload.get("status")
                    if status == "input_error":
                        raise FatalToolError(
                            f"{script}: input error: {payload.get('message') or 'unspecified'}",
                            script=script,
                            exit_code=0,
                            stderr_tail=_tail(raw.stderr),
                        )
                    if status == "internal_error":
                        raise ToolInfraError(
                            f"{script}: internal error: {payload.get('message') or 'unspecified'}",
                            script=script,
                            exit_code=0,
                            stderr_tail=_tail(raw.stderr),
                        )
                    result = parse(payload)
                except FatalToolError:
                    raise
                except ToolInfraError as exc:
                    failure = exc
                    continue
                except (SandboxOutputError, ValueError, TypeError) as exc:
                    # ValueError covers pydantic.ValidationError. Output we cannot parse is infrastructure, not a test failure.
                    failure = ToolInfraError(
                        f"{script}: unusable output: {_tail(str(exc), 300)}",
                        script=script,
                        exit_code=0,
                        stderr_tail=_tail(raw.stderr),
                    )
                    continue
                if result.duration_ms == 0:
                    result = result.model_copy(update={"duration_ms": raw.duration_ms})
                return result

            failure = ToolInfraError(
                f"{script}: sandbox exited with code {raw.exit_code}: {self._message_of(raw)}",
                script=script,
                exit_code=raw.exit_code,
                stderr_tail=_tail(raw.stderr),
            )
            log.warning("%s", failure)

        assert failure is not None
        raise failure

    @staticmethod
    def _message_of(raw: RawOutput) -> str:
        """Best short reason for a failed call: the JSON message if there is one, else the stderr tail."""
        try:
            message = parse_stdout_json(raw.stdout).get("message")
            if message:
                return _tail(str(message), 300)
        except (SandboxOutputError, ValueError):
            pass
        return _tail(raw.stderr.strip() or raw.stdout.strip() or "no output", 300)
