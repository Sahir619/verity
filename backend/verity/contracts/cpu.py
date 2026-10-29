"""CPU Lab job wire models."""

from typing import Literal

from pydantic import Field, SerializeAsAny, field_validator

from .api import RunSummaryItem
from .base import WireModel, opt
from .cpu_profile import CpuProfile
from .events import Priority
from .signoff import EqyVsV1


class CreateCpuJobRequest(WireModel):
    spec_id: Literal["rv32i_micro"]
    goal: Priority
    thread_id: str | None = opt()

    @field_validator("thread_id")
    @classmethod
    def valid_thread(cls, value: str | None) -> str | None:
        from . import normalize_thread_id

        if value is None:
            return None
        normal = normalize_thread_id(value)
        if normal is None:
            raise ValueError("threadId must be a UUID")
        return normal


class CpuEvidence(WireModel):
    """What a winner's sign-off sheet and sim result say, read by code. Null where the run did not report it."""

    eqy_vs_v1: EqyVsV1 | None = None
    tests_passed: int | None = None
    tests_total: int | None = None
    seeds_passed: int | None = None
    seeds_run: int | None = None
    mutants_killed: int | None = None
    mutants_total: int | None = None
    repairs: int | None = None
    techniques: list[str] | None = None
    # riscv-formal on a core. Null when it did not run: "not measured", never guessed.
    # rvfi_timeouts counts checks not proven within the time limit; rvfi_failed names the checks that failed.
    rvfi_passed: int | None = None
    rvfi_total: int | None = None
    rvfi_timeouts: int | None = None
    rvfi_failed: list[str] | None = None


# "ran" or "reused": the block run read this research. "off": no research service is configured.
# "skipped" or "failed": the block ran without research.
CpuResearchState = Literal["pending", "ran", "reused", "skipped", "failed", "off"]


class CpuBlockView(WireModel):
    block_id: str
    pack_id: str
    title: str
    status: Literal["waiting", "running", "passed", "failed"] = "waiting"
    run_id: str | None = None
    attempts: int = 0
    summary: RunSummaryItem | None = None
    evidence: CpuEvidence = Field(default_factory=CpuEvidence)
    research: CpuResearchState = "pending"
    research_id: str | None = None
    # "v1" when the run's winner was not proven equal to its own v1, so the CPU uses v1 instead.
    used_version: str | None = None
    # True in an upgrade job for a block taken from the parent job: no run was made, run_id and evidence are the parent's.
    reused: bool = False


class CpuCoreView(WireModel):
    pack_id: str | None = None
    run_id: str | None = None
    status: Literal["locked", "running", "passed", "failed"] = "locked"
    summary: RunSummaryItem | None = None
    evidence: CpuEvidence = Field(default_factory=CpuEvidence)


class CpuTotals(WireModel):
    """Summed by code over the block winners of one CPU version. Area and power are sums; the clock is the longest
    block clock period (the critical path). A part is null when any block's value is unknown."""

    area_um2: float | None = None
    clock_ns: float | None = None
    power_mw: float | None = None


class CpuVersionView(WireModel):
    """CPU v1 is the first core. Each later version swaps in one improved block winner and reruns the core."""

    version: Literal[1, 2, 3]
    core_run_id: str | None = None
    changed_block: str | None = None
    block_run_id: str | None = None
    totals: CpuTotals = Field(default_factory=CpuTotals)
    core_summary: RunSummaryItem | None = None
    core_evidence: CpuEvidence = Field(default_factory=CpuEvidence)
    block_evidence: CpuEvidence | None = None
    # Benchmarks on the core winner. profile: fast memory (wait 0). flash_profile: flash-like
    # memory (wait 3), null when the core cannot run with waits. Null means not measured; profile_note says why.
    profile: CpuProfile | None = None
    flash_profile: CpuProfile | None = None
    profile_note: str | None = None
    rvfi_passed: int | None = None
    rvfi_total: int | None = None
    rvfi_timeouts: int | None = None
    rvfi_failed: list[str] | None = None


class BenchDelta(WireModel):
    """One benchmark in the parent's CPU v1 and in the upgraded core. Null where either side is not measured."""

    name: str
    parent_cycles: int | None = None
    cycles: int | None = None
    cycles_delta_pct: float | None = None
    parent_cpi: float | None = None
    cpi: float | None = None


class ProfileComparison(WireModel):
    """One memory mode. Every number is computed by code from the two measured profiles."""

    wait_cycles: int
    parent: CpuProfile | None = None
    child: CpuProfile | None = None
    cpi_delta_pct: float | None = None
    bench_time_ratio: float | None = None
    bench: list[BenchDelta] = Field(default_factory=list)


class CpuComparison(WireModel):
    """An upgrade job against its parent's CPU v1. Area is the sum over the block winners; the clock is the longest
    block clock period. fast: wait 0. flash: wait 3, null when either core has no flash-like profile."""

    parent_area_um2: float | None = None
    area_um2: float | None = None
    area_delta_pct: float | None = None
    parent_clock_ns: float | None = None
    clock_ns: float | None = None
    clock_delta_pct: float | None = None
    fast: ProfileComparison | None = None
    flash: ProfileComparison | None = None


class CpuJobView(WireModel):
    id: str
    spec_id: Literal["rv32i_micro", "rv32i_pipe2", "rv32i_cached"] = "rv32i_micro"
    goal: Priority
    status: Literal["running", "integrating", "improving", "passed", "failed", "stopped"] = "running"
    created_at: str
    finished_at: str | None = None
    spent_usd: float = 0.0
    message: str | None = None
    blocks: list[CpuBlockView]
    core: CpuCoreView = Field(default_factory=CpuCoreView)
    versions: list[CpuVersionView] = Field(default_factory=list)
    # Upgrade jobs only: the passed job this one upgrades, the catalog id, and the numbers
    # next to the parent's CPU v1. Null on a plain job.
    parent_job_id: str | None = None
    upgrade_id: str | None = None
    comparison: CpuComparison | None = None
    # A core retry only: the failed job whose seven passed blocks this job reuses. Null otherwise.
    retry_of: str | None = None
    # The newest core retry started from this job, in the owner's own view only (null otherwise, and when there is none).
    # Set on each read, never stored.
    retry_job_id: str | None = None
    # The chat the job was started from, so its board link can reopen that chat. Null for a job started outside a chat.
    thread_id: str | None = None


class CpuJobsResponse(WireModel):
    jobs: list[CpuJobView]


class UpgradeSource(WireModel):
    title: str
    url: str


class Prediction(WireModel):
    kind: Literal["estimate"] = "estimate"
    cpi: float | None
    cpi_delta: float | None
    area_delta_pct: float
    clock_delta_pct: float
    area_um2: float | None
    clock_ns: float | None
    time_ratio: float | None
    power_ratio: float
    formula: str
    assumptions: str
    # The memory wait the CPI numbers are relative to: 0 fast memory, 3 flash-like memory (cache_16).
    basis_wait_cycles: int = 0


class UpgradeOption(WireModel):
    id: str
    label: str
    targets: list[str]
    prediction: Prediction
    sources: list[UpgradeSource]
    needs: list[str]
    blocks: list[str]
    contract_parts: list[str]
    buildable: bool = False
    recommended: bool = False
    reason: str = "Not buildable yet"


class UpgradesResponse(WireModel):
    profile: SerializeAsAny[CpuProfile] | None = None
    flash_profile: SerializeAsAny[CpuProfile] | None = None
    options: list[UpgradeOption] = Field(default_factory=list)
    message: str


class UpgradeRequest(WireModel):
    upgrade_id: str = Field(min_length=1, max_length=64)
