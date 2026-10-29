"""Test doubles and helpers shared by the test files.

FakeToolRunner implements the ToolRunner protocol with no sandbox: a design passes `verify` when its text equals the
pack's reference.v (a leading `// verity-fake:` header line is ignored, which is how FakeProvider marks its variants),
`measure` returns deterministic numbers derived from the RTL hash, and `check_equivalence` returns a fixed status.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from verity.config import AGENT_ROOT, Settings, load_settings
from verity.contracts import EventBase, parse_event
from verity.core.budget import BudgetLimits
from verity.core.context import RunContext, RunOutcome
from verity.core.pack import TaskPack
from verity.core.records import RunRecord
from verity.core.results import (
    ContractResult,
    EquivalenceResult,
    GoldenCheckFailure,
    GoldenCheckResult,
    LintResult,
    MeasureResult,
    MutantRun,
    SeedRun,
    SeedsResult,
    SimCase,
    SimResult,
    StrengthResult,
    SynthResult,
    TimingResult,
    VerifyResult,
    WaveResult,
    WaveSignal,
    WaveTrace,
)
from verity.contracts.cpu_profile import BenchRow, CyclesByStall
from verity.core.rtl import Rtl, normalize_rtl_text
from verity.llm.fake import FakeProvider
from verity.llm.gateway import RunLLM
from verity.orchestrator import run_orchestrator
from verity.roles import default_roles
from verity.runs.hub import EventHub
from verity.runs.ledger import RunLedger
from verity.store import SqliteStore
from verity.tools.bench import BenchResult
from verity.tools.rvfi import RvfiCheck, RvfiResult

REPO_ROOT = AGENT_ROOT.parent
WEB_ROOT = REPO_ROOT / "frontend"
GOLDEN_JSON = WEB_ROOT / "public" / "runs" / "golden.json"
FAKE_HEADER = "// verity-fake:"


def make_settings(data_dir: Path, *, gate_ms: int = 0, **top_level: Any) -> Settings:
    base = load_settings(provider="fake")
    plan = base.plan.model_copy(update={"gate_auto_continue_ms": gate_ms, "gate_after_replan_ms": 0})
    # Never a developer's Supabase keys: tests keep SQLite, and sign-in tokens are checked only where a test asks for it.
    local = {"supabase": type(base.supabase)(), "threads": base.threads.model_copy(update={"store": "sqlite"})}
    # One baseline sample by default so call counts stay simple; tests of R4 pass baseline=... themselves.
    one_sample = base.baseline.model_copy(update={"samples": 1})
    return base.model_copy(update={"data_dir": data_dir, "plan": plan, "baseline": one_sample, **local, **top_level})


def tmp_cassettes(settings: Settings) -> Path:
    """An empty cassette folder next to the data dir, so a developer's real cassettes never leak into a test."""
    path = settings.data_dir.parent / "no-cassettes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def strip_fake_header(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not line.startswith(FAKE_HEADER))


class FakeToolRunner:
    """`survivors`: how many of the last planted bugs pass the tests in `test_strength` (default none, so the sign-off
    makes no extra EQY call). `waves`: False makes `waveform` report a failure (no waveform)."""

    def __init__(self, *, eqy_status: str = "proven", delay_s: float = 0.0, survivors: int = 0, waves: bool = True) -> None:
        self.eqy_status = eqy_status
        self.delay_s = delay_s
        self.survivors = survivors
        self.waves = waves
        self.verify_calls = 0
        self.measure_calls = 0
        self.eqy_calls = 0
        self.seeds_calls = 0
        self.strength_calls = 0
        self.wave_calls: list[str | None] = []  # the case asked for, per call
        self.mutants_seen: list[str] = []
        self._first_measure: str | None = None
        self.sandbox_down: str | None = None  # a plain sentence makes /health report sandbox_down
        # CPU Lab: bench and rvfi. A BenchResult in bench_results[wait] (or an Exception to raise) replaces the default
        # passing suite; rvfi_checks is [(name, status)] and rvfi_error an Exception to raise.
        self.bench_calls: list[tuple[str, int]] = []
        self.rvfi_calls: list[str] = []
        self.bench_results: dict[int, BenchResult | Exception] = {}
        self.rvfi_checks: list[tuple[str, str]] = [("insn_add", "pass"), ("reg", "pass"), ("pc_fwd", "pass")]
        self.rvfi_error: Exception | None = None

    async def image_digest(self) -> str:
        return "sha256:fake-image"

    async def sandbox_problem(self) -> str | None:
        return self.sandbox_down

    async def verify(self, rtl: Rtl, pack: TaskPack) -> VerifyResult:
        self.verify_calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        reference = normalize_rtl_text(pack.reference_rtl() or "")
        ok = normalize_rtl_text(strip_fake_header(rtl.text)) == reference
        n = pack.test_count
        if ok:
            return VerifyResult(
                status="pass",
                contract=ContractResult(ok=True),
                lint=LintResult(ok=True, warnings=0, tool="fake lint"),
                sim=SimResult(ok=True, total=n, passed=n, tool="fake sim"),
                duration_ms=7,
            )
        case = SimCase(name="acc_case", inputs="a=[1,2,3,4]", expected="0x10", got="0x00", pass_=False, cycle=3)
        return VerifyResult(
            status="sim_fail",
            contract=ContractResult(ok=True),
            lint=LintResult(ok=True, warnings=0, tool="fake lint"),
            sim=SimResult(ok=False, total=n, passed=n - 2, failure_count=2, failures=[case], tool="fake sim"),
            duration_ms=7,
        )

    async def measure(self, rtl: Rtl, pack: TaskPack) -> MeasureResult:
        self.measure_calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self._first_measure is None:
            self._first_measure = rtl.sha256
            area, clock = 1000.0, 3.0
        else:
            digest = hashlib.sha256(rtl.text.encode()).hexdigest()
            area = 1000.0 - 20 * (1 + int(digest[:2], 16) % 5)
            clock = 3.0 + (int(digest[2:4], 16) % 5) / 10
        return MeasureResult(
            status="ok",
            synth=SynthResult(ok=True, area_um2=area, cell_count=100, cells={"NAND2_X1": 100}, tool="fake yosys"),
            sta=TimingResult(
                ok=True,
                clock_period_ns=clock,
                wns_ns=pack.target_clock_ns - clock,
                target_period_ns=pack.target_clock_ns,
                tool="fake sta",
            ),
            duration_ms=9,
        )

    async def bench(self, rtl: Rtl, pack: TaskPack, wait_cycles: int = 0) -> BenchResult:
        """A passing three-benchmark suite: 100 instructions each, 4 cycles each with wait 0 (CPI 4 as on the
        multicycle core) or 5 with waits; the pipelined top is `not_supported` with waits, as the real bin/bench is."""
        self.bench_calls.append((pack.top, wait_cycles))
        scripted = self.bench_results.get(wait_cycles)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            return scripted
        if wait_cycles and pack.top == "rv32i_pipe2_core":
            return BenchResult(status="not_supported", wait_cycles=wait_cycles, message="waits not supported")
        cycles = 500 if wait_cycles else 400
        rows = [
            BenchRow(
                name=name, instructions=100, cycles=cycles, cpi=cycles / 100, passed=True, self_check=1,
                cycles_by_stall=CyclesByStall(busy=300, fetch=50 + (50 if wait_cycles else 0), data=50),
            )
            for name in ("crc32", "matmult-int", "edn")
        ]
        return BenchResult(status="pass", wait_cycles=wait_cycles, bench=rows)

    async def rvfi(self, rtl: Rtl, pack: TaskPack) -> RvfiResult:
        self.rvfi_calls.append(pack.top)
        if self.rvfi_error is not None:
            raise self.rvfi_error
        checks = [RvfiCheck(name=name, status=status) for name, status in self.rvfi_checks]
        passed = sum(c.status == "pass" for c in checks)
        statuses = {c.status for c in checks}
        status = "fail" if "fail" in statuses else "timeout" if "timeout" in statuses else "error" if not checks or "error" in statuses else "ok"
        return RvfiResult(status=status, passed=passed, total=len(checks), checks=checks)

    async def check_equivalence(self, gold: Rtl, gate: Rtl, pack: TaskPack) -> EquivalenceResult:
        self.eqy_calls += 1
        return EquivalenceResult(status=self.eqy_status, log="fake eqy log", duration_ms=11)  # type: ignore[arg-type]

    def _passes(self, rtl: Rtl, pack: TaskPack) -> bool:
        return normalize_rtl_text(strip_fake_header(rtl.text)) == normalize_rtl_text(pack.reference_rtl() or "")

    async def verify_seeds(self, rtl: Rtl, pack: TaskPack, seeds) -> SeedsResult:
        self.seeds_calls += 1
        n = pack.test_count
        ok = self._passes(rtl, pack)
        runs = [SeedRun(seed=s, ok=ok, total=n, passed=n if ok else n - 1, failure_count=0 if ok else 1) for s in seeds]
        lint = LintResult(ok=True, warnings=1, by_tag={"UNUSEDSIGNAL": 1}, latches=0, tool="fake lint")
        return SeedsResult(status="pass" if ok else "sim_fail", lint=lint, seeds=runs, duration_ms=13)

    async def test_strength(self, rtl: Rtl, pack: TaskPack, mutants) -> StrengthResult:
        self.strength_calls += 1
        self.mutants_seen = [m.id for m in mutants]
        keep = len(mutants) - self.survivors
        rows = [
            MutantRun(id=m.id, status="caught" if i < keep else "survived", detail="fake", passed=0, total=pack.test_count)
            for i, m in enumerate(mutants)
        ]
        return StrengthResult(status="ok", mutants=rows, duration_ms=17)

    async def waveform(self, rtl: Rtl, pack: TaskPack, case: str | None = None) -> WaveResult:
        self.wave_calls.append(case)
        if not self.waves:
            return WaveResult(status="sim_error", message="fake: no waveform")
        failing = not self._passes(rtl, pack)
        trace = WaveTrace(
            case=case or "first_case",
            fail_cycle=3 if failing else None,
            start_cycle=1 if failing else 0,
            signals=[
                WaveSignal(name="rst_n", width=1, got=["1", "1", "1", "1"]),
                WaveSignal(name="q", width=8, got=["0x01", "0x02", "0x04", "0x05"], expected=["0x01", "0x02", "0x03", ""]),
            ],
            note="Fake waveform.",
        )
        return WaveResult(status="ok", wave=trace, duration_ms=5)

    async def check_golden(self, golden_py: str, cases) -> GoldenCheckResult:
        """Runs the draft in-process (no Docker in a unit test) with the same contract as the real sandbox script:
        golden_step(state, inputs, rst) -> (next_state, outputs), state reset to {} at the start of each case."""
        namespace: dict = {}
        try:
            exec(compile(golden_py, "<golden.py>", "exec"), namespace)  # noqa: S102  (test-only, no untrusted input)
            golden_step = namespace["golden_step"]
        except Exception as exc:  # noqa: BLE001
            return GoldenCheckResult(status="fail", message=f"{type(exc).__name__}: {exc}")
        failures = []
        passed = 0
        for case in cases:
            state: dict = {}
            ok = True
            for n, step in enumerate(case.get("steps", []), start=1):
                try:
                    state, outputs = golden_step(state, step.get("inputs", {}), bool(step.get("rst", False)))
                except Exception as exc:  # noqa: BLE001
                    failures.append(GoldenCheckFailure(name=case.get("name", ""), expected="no exception", got=str(exc), step=n))
                    ok = False
                    break
                expected = step.get("expected", {})
                if any(outputs.get(k) != v for k, v in expected.items()):
                    failures.append(GoldenCheckFailure(name=case.get("name", ""), expected=str(expected), got=str(outputs), step=n))
                    ok = False
                    break
            if ok:
                passed += 1
        total = len(cases)
        return GoldenCheckResult(status="pass" if passed == total else "fail", total=total, passed=passed, failure_count=total - passed, failures=failures)


@dataclass(slots=True)
class FakeRun:
    ctx: RunContext
    events: list[EventBase]
    outcome: RunOutcome | None
    store: SqliteStore
    run_id: str
    tools: FakeToolRunner
    error: BaseException | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def execute_fake_run(
    settings: Settings,
    pack: TaskPack,
    *,
    mode: str = "full",
    priority: str | None = None,
    steers: tuple[Any, ...] = (),
    tools: FakeToolRunner | None = None,
    provider: FakeProvider | None = None,
    cassette_dir: Path | None = None,
    on_ctx: Callable[[RunContext], None] | None = None,
) -> FakeRun:
    """Run the real orchestrator once with the fake provider and the fake tool runner. Steers are queued up front.

    The returned FakeRun keeps its store open (the caller may read more from it); call `await run.store.close()`.
    """
    store = SqliteStore(settings)
    await store.open()
    tools = tools or FakeToolRunner()
    provider = provider or FakeProvider(settings, delay_s=0.0, cassette_dir=cassette_dir or tmp_cassettes(settings))
    chosen = priority or pack.default_priority
    limits = BudgetLimits.from_settings(settings, pack.budget)
    ledger = RunLedger(limits, settings)
    hub = EventHub(store, settings)
    run_id = "r_test_" + uuid.uuid4().hex[:12]
    ctx = RunContext(
        run_id=run_id,
        session_id="test",
        pack=pack,
        priority=chosen,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        settings=settings,
        llm=RunLLM(provider, settings, ledger),
        roles=default_roles(),
        tools=tools,
        ledger=ledger,
        sink=hub,
        store=store,
    )
    now = datetime.now(UTC)
    await store.create_run(
        RunRecord(
            id=run_id,
            session_id="test",
            preset_id=pack.id,
            priority=chosen,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            status="running",
            created_at=now,
            started_at=now,
            budget=limits,
            task_pack_sha256=pack.sha256,
        )
    )
    hub.open_run(run_id, started_monotonic=ctx.started_monotonic, ledger=ledger, ctx=ctx)
    for steer in steers:
        ctx.steer.put_nowait(steer)
    if on_ctx is not None:
        on_ctx(ctx)
    error: BaseException | None = None
    try:
        await run_orchestrator(ctx)
    except Exception as exc:  # surfaced to the test through FakeRun.error
        error = exc
    events = await store.events_after(run_id, 0)
    hub.close_run(run_id)
    return FakeRun(ctx=ctx, events=events, outcome=ctx.outcome, store=store, run_id=run_id, tools=tools, error=error)


# ---------- the recorded real run the ask and thread tests talk about ----------
FIXTURE_RUN_ID = "r_27fed084a749432ba42ca6981f3cc336"  # cnt8, 10 bits, goal speed; tests/fixtures/<id>/
FIXTURE_RUN_DIR = Path(__file__).resolve().parent / "fixtures" / FIXTURE_RUN_ID


async def seed_fixture_run(store: SqliteStore, *, session_id: str, status: str | None = None) -> RunRecord:
    """The real run: its row, its stored events and the report files the ask tools read, owned by `session_id`."""
    data = json.loads((FIXTURE_RUN_DIR / "run.json").read_text(encoding="utf-8"))
    data["session_id"] = session_id
    if status is not None:
        data["status"] = status
    run = RunRecord.model_validate(data)
    await store.create_run(run)
    for event in json.loads((FIXTURE_RUN_DIR / "runlog.json").read_text(encoding="utf-8"))["events"]:
        await store.append_event(run.id, parse_event(event))
    for path in sorted(FIXTURE_RUN_DIR.rglob("*")):
        rel = path.relative_to(FIXTURE_RUN_DIR).as_posix()
        if path.is_file() and rel.startswith(("candidates/", "final/")):
            await store.write_run_file(run.id, rel, path.read_bytes(), "text/plain")
    return run
