"""Parsed sandbox results and their mapping onto the wire contract.

The JSON shapes below are the EXACT stdout contract of /verity/bin/verify, /verity/bin/measure,
/verity/bin/eqy, and the sign-off and waveform scripts /verity/bin/seeds, strength and wave (CONTRACTS.md section
3). The sandbox scripts cannot import this package, so any change here must be made in CONTRACTS.md and the scripts
in the same change. Keys are snake_case, except the embedded
`circuit` object and test cases, which already use the wire format.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidatorFunctionWrapHandler, field_validator

from ..contracts import PPA, CircuitGraph, Equivalence, TestCase, Verification

MAX_FAILURES = 5
LINT_LOG_CAP = 4_000
REPORT_LOG_CAP = 8_000


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def _cap(text: str, limit: int, *, keep: Literal["head", "tail"] = "head") -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]" if keep == "head" else "[truncated]\n" + text[-limit:]


# ---------- verify: contract + lint + sim ----------
VerifyStatus = Literal[
    "pass", "contract_fail", "lint_fail", "sim_fail", "sim_error", "timeout", "input_error", "internal_error"
]


class ParsedPort(_Result):
    name: str
    dir: Literal["in", "out", "inout"]
    width: int  # total bits of the Verilog vector
    signed: bool = False


class ContractResult(_Result):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    ports: list[ParsedPort] = Field(default_factory=list)


class LintResult(_Result):
    ok: bool
    errors: int = 0
    warnings: int = 0
    by_tag: dict[str, int] = Field(default_factory=dict)  # warnings per Verilator tag; empty from older images
    latches: int | None = None  # LATCH warnings; None from older images
    log: str = ""
    tool: str = "Verilator"


class SimCase(_Result):
    """One failing case. Same field names as the wire TestCase."""

    name: str
    inputs: str
    expected: str
    got: str
    pass_: bool = Field(default=False, alias="pass")
    cycle: int | None = None

    def to_test_case(self) -> TestCase:
        return TestCase(
            name=self.name, inputs=self.inputs, expected=self.expected, got=self.got, pass_=self.pass_, cycle=self.cycle
        )


class SimResult(_Result):
    ok: bool
    total: int
    passed: int
    failure_count: int = 0
    failures: list[SimCase] = Field(default_factory=list)  # first MAX_FAILURES failing cases, in case order
    seed: int | None = None
    log: str = ""
    tool: str = "iverilog + cocotb"


class VerifyResult(_Result):
    schema_: Literal["verity.verify/1"] = Field(default="verity.verify/1", alias="schema")
    status: VerifyStatus
    contract: ContractResult | None = None
    lint: LintResult | None = None
    sim: SimResult | None = None
    duration_ms: int = 0
    message: str = ""  # human-readable reason for sim_error / timeout / input_error / internal_error
    tool_versions: dict[str, str] = Field(default_factory=dict)

    def passed_all(self, expected_total: int) -> bool:
        """PASS only if the testbench ran the full vector count from task.toml (ARCHITECTURE 13)."""
        return (
            self.status == "pass"
            and self.sim is not None
            and self.sim.ok
            and self.sim.total == expected_total
            and self.sim.passed == expected_total
        )

    def to_verification(self, expected_total: int, *, timeout_s: int | None = None) -> Verification:
        """Map onto the wire Verification. `expected_total` is TaskPack.test_count."""
        lint_tool = self.lint.tool if self.lint else "Verilator"
        if self.status == "contract_fail":
            errors = self.contract.errors if self.contract else [self.message or "interface check failed"]
            return Verification(
                lint="fail",
                lint_log=_cap("Interface check failed:\n" + "\n".join(f"- {e}" for e in errors), LINT_LOG_CAP),
                passed=0,
                total=expected_total,
                failures=[],
                tool="Yosys interface check",
                duration_ms=self.duration_ms,
            )
        if self.status == "lint_fail":
            return Verification(
                lint="fail",
                lint_log=_cap(self.lint.log if self.lint else self.message, LINT_LOG_CAP),
                passed=0,
                total=expected_total,
                failures=[],
                tool=lint_tool,
                duration_ms=self.duration_ms,
            )

        lint_log = None
        if self.lint is not None:
            summary = f"Verilator: {self.lint.warnings} warning{'s' if self.lint.warnings != 1 else ''}"
            lint_log = _cap(summary + ("\n" + self.lint.log if self.lint.log and self.lint.warnings else ""), LINT_LOG_CAP)
        sim_tool = self.sim.tool if self.sim else "iverilog + cocotb"

        if self.status in ("sim_error", "timeout") or self.sim is None:
            if self.status == "timeout":
                case = TestCase(
                    name="simulation_timeout",
                    inputs="all cases",
                    expected=f"finishes within {timeout_s} s" if timeout_s else "finishes in time",
                    got="timed out",
                    pass_=False,
                )
            else:
                first = (self.message or (self.sim.log if self.sim else "") or "simulation did not run").strip()
                case = TestCase(
                    name="simulation_error",
                    inputs="all cases",
                    expected="simulation compiles and runs",
                    got=_cap(first.splitlines()[0] if first else "unknown error", 300),
                    pass_=False,
                )
            return Verification(
                lint="pass",
                lint_log=lint_log,
                passed=0,
                total=expected_total,
                failures=[case],
                tool=sim_tool,
                duration_ms=self.duration_ms,
            )

        failures = [c.to_test_case() for c in self.sim.failures[:MAX_FAILURES]]
        passed = self.sim.passed
        if self.sim.total != expected_total:
            # A truncated or padded test run can never count as a pass.
            passed = min(passed, expected_total - 1) if expected_total > 0 else 0
            failures.insert(
                0,
                TestCase(
                    name="vector_count_mismatch",
                    inputs="testbench",
                    expected=f"{expected_total} cases",
                    got=f"{self.sim.total} cases",
                    pass_=False,
                ),
            )
        return Verification(
            lint="pass",
            lint_log=lint_log,
            passed=max(0, passed),
            total=expected_total,
            failures=failures[:MAX_FAILURES],
            tool=sim_tool,
            duration_ms=self.duration_ms,
        )


# ---------- measure: synth + sta + circuit ----------
MeasureStatus = Literal["ok", "synth_fail", "sta_fail", "timeout", "input_error", "internal_error"]


class SynthResult(_Result):
    ok: bool
    area_um2: float | None = None
    cell_count: int | None = None
    cells: dict[str, int] = Field(default_factory=dict)  # liberty cell name -> count
    cell_area: dict[str, float] = Field(default_factory=dict)  # liberty cell name -> total area (um2); empty from older images
    log: str = ""
    tool: str = "Yosys"


class CriticalPath(_Result):
    """The worst setup path of `report_checks`. A register end is named by the net on its flip-flop's Q pin."""

    startpoint: str
    endpoint: str
    start_kind: str = ""  # register | input | output | other
    end_kind: str = ""
    depth: int = 0  # combinational cells on the path
    delay_ns: float | None = None  # data arrival time


class TimingResult(_Result):
    ok: bool
    clock_period_ns: float | None = None  # minimum period = target_period_ns - worst setup slack
    wns_ns: float | None = None
    target_period_ns: float | None = None
    clocked: bool = True  # false: combinational design measured against a virtual clock
    path: CriticalPath | None = None  # absent from older images
    log: str = ""
    tool: str = "OpenSTA"

    @field_validator("path", mode="wrap")
    @classmethod
    def _lenient_path(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> CriticalPath | None:
        """A malformed path never invalidates the timing numbers: it becomes None."""
        try:
            return handler(value)
        except ValidationError:
            return None


class PowerResult(_Result):
    """OpenSTA `report_power` estimate (not signoff). Measured at a fixed reference clock so deltas vs v1 are fair."""

    ok: bool
    power_mw: float | None = None
    internal_mw: float | None = None
    switching_mw: float | None = None
    leakage_mw: float | None = None
    method: str = "vectorless"  # how switching activity was set: "vectorless" (default input activity), never a guess
    input_activity: float | None = None  # toggles per clock cycle on every non-clock input
    reference_clock_ns: float | None = None  # the pack's target_clock_ns, identical for v1 and every child
    log: str = ""
    tool: str = "OpenSTA"


DEFAULT_PDK = "Nangate45"


class MeasureResult(_Result):
    schema_: Literal["verity.measure/1"] = Field(default="verity.measure/1", alias="schema")
    status: MeasureStatus
    synth: SynthResult | None = None
    sta: TimingResult | None = None
    power: PowerResult | None = None  # absent in results from older images: power stays null
    circuit: CircuitGraph | None = None
    pdk: str = DEFAULT_PDK
    duration_ms: int = 0
    message: str = ""
    tool_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("circuit", mode="wrap")
    @classmethod
    def _lenient_circuit(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> CircuitGraph | None:
        """A malformed or oversized circuit graph never invalidates the PPA numbers: it becomes None."""
        try:
            return handler(value)
        except ValidationError:
            return None

    @property
    def measured(self) -> bool:
        """A candidate counts as measured when synthesis produced an area. Timing may be missing (null)."""
        return self.synth is not None and self.synth.ok and self.synth.area_um2 is not None

    def to_ppa(self) -> PPA:
        tools = []
        if self.synth is not None:
            tools.append(self.synth.tool)
        if self.sta is not None and self.sta.ok:
            tools.append(self.sta.tool)
        return PPA(
            area_um2=round(self.synth.area_um2, 2) if self.synth and self.synth.area_um2 is not None else None,
            cell_count=self.synth.cell_count if self.synth else None,
            clock_period_ns=(
                round(self.sta.clock_period_ns, 3)
                if self.sta is not None and self.sta.ok and self.sta.clock_period_ns is not None
                else None
            ),
            power_mw=(
                round(self.power.power_mw, 4)
                if self.power is not None and self.power.ok and self.power.power_mw is not None
                else None
            ),
            tools=tools,
            pdk=self.pdk,
        )

    def circuit_for(self, candidate_id: str) -> CircuitGraph | None:
        if self.circuit is None:
            return None
        return self.circuit.model_copy(update={"candidate_id": candidate_id})


# ---------- eqy ----------
EqyRawStatus = Literal["proven", "not_equivalent", "inconclusive", "timeout", "error"]


class EquivalenceResult(_Result):
    schema_: Literal["verity.eqy/1"] = Field(default="verity.eqy/1", alias="schema")
    status: EqyRawStatus
    log: str = ""
    duration_ms: int = 0
    message: str = ""
    tool: str = "yosys-eqy"
    tool_versions: dict[str, str] = Field(default_factory=dict)

    def to_equivalence(self, against_id: str) -> Equivalence:
        """timeout and error never become proven (ARCHITECTURE 11.5): both map to inconclusive."""
        status = self.status if self.status in ("proven", "not_equivalent") else "inconclusive"
        text = self.log or self.message
        return Equivalence(
            status=status,
            against_id=against_id,
            log=_cap(text, REPORT_LOG_CAP, keep="tail") if text else None,
        )


def not_run_equivalence(against_id: str, reason: str) -> Equivalence:
    return Equivalence(status="not_run", against_id=against_id, log=reason)


# ---------- sign-off: more seeds (verity.seeds/1) ----------
SeedsStatus = Literal["pass", "sim_fail", "lint_fail", "sim_error", "timeout", "input_error", "internal_error"]


class SeedRun(_Result):
    seed: int
    ok: bool
    total: int = 0
    passed: int = 0
    failure_count: int = 0
    failures: list[SimCase] = Field(default_factory=list)  # the first failing case only
    timed_out: bool = False
    message: str = ""


class SeedsResult(_Result):
    schema_: Literal["verity.seeds/1"] = Field(default="verity.seeds/1", alias="schema")
    status: SeedsStatus
    lint: LintResult | None = None
    seeds: list[SeedRun] = Field(default_factory=list)
    duration_ms: int = 0
    message: str = ""
    tool_versions: dict[str, str] = Field(default_factory=dict)


# ---------- sign-off: test strength (verity.strength/1) ----------
MutantStatus = Literal["caught", "survived", "invalid", "not_run"]


class MutantRun(_Result):
    id: str
    status: MutantStatus
    detail: str = ""
    passed: int = 0
    total: int = 0
    duration_ms: int = 0


class StrengthResult(_Result):
    schema_: Literal["verity.strength/1"] = Field(default="verity.strength/1", alias="schema")
    status: Literal["ok", "timeout", "input_error", "internal_error"]
    mutants: list[MutantRun] = Field(default_factory=list)
    duration_ms: int = 0
    message: str = ""
    tool_versions: dict[str, str] = Field(default_factory=dict)


# ---------- waveform of one test case (verity.wave/1) ----------
WaveStatus = Literal["ok", "no_case", "compile_error", "sim_error", "timeout", "input_error", "internal_error"]


class WaveSignal(_Result):
    name: str
    width: int
    got: list[str]
    expected: list[str] | None = None  # same length as got; "" where nothing is expected


class WaveTrace(_Result):
    case: str
    fail_cycle: int | None = None  # None: the case passed (the wave shows its start)
    start_cycle: int = 0
    signals: list[WaveSignal] = Field(default_factory=list)
    note: str = ""


class WaveResult(_Result):
    schema_: Literal["verity.wave/1"] = Field(default="verity.wave/1", alias="schema")
    status: WaveStatus
    wave: WaveTrace | None = None
    duration_ms: int = 0
    message: str = ""
    tool_versions: dict[str, str] = Field(default_factory=dict)


# ---------- golden_check: CE3's pure-Python self-check of a drafted golden model ----------
GoldenCheckStatus = Literal["pass", "fail", "timeout", "input_error", "internal_error"]


class GoldenCheckFailure(_Result):
    name: str
    expected: str
    got: str
    step: int | None = None


class GoldenCheckResult(_Result):
    schema_: Literal["verity.golden_check/1"] = Field(default="verity.golden_check/1", alias="schema")
    status: GoldenCheckStatus
    total: int = 0
    passed: int = 0
    failure_count: int = 0
    failures: list[GoldenCheckFailure] = Field(default_factory=list)
    message: str = ""
    duration_ms: int = 0

    def passed_all(self, expected_total: int) -> bool:
        return self.status == "pass" and self.total == expected_total and self.passed == expected_total
