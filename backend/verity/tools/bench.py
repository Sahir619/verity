"""Strict sandbox benchmark parsing and measured profile construction."""

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..contracts.cpu_profile import BenchRow, CpuProfile, CyclesByStall

BENCH_NAMES = ("crc32", "matmult-int", "edn")
BENCH_TIMEOUT_S = 900  # flash-like memory is about 700M cycles; a Nemotron core needed more than 300 s (2026-10-28)


class BenchResult(BaseModel):
    schema_: Literal["verity.bench/1"] = Field(default="verity.bench/1", alias="schema")
    status: Literal["pass", "fail", "compile_error", "sim_error", "timeout", "not_supported"]
    wait_cycles: int = Field(default=0, ge=0, strict=True)
    bench: list[BenchRow] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    message: str | None = None

    def profile(
        self, *, clock_ns: float | None = None, area_by_block_um2: dict[str, float] | None = None,
        critical_path_blocks: list[str] | None = None,
    ) -> CpuProfile:
        """Never invent timing or path evidence when measurement is unavailable."""
        if self.status != "pass":
            raise ValueError("a CPU profile requires a complete passing benchmark suite")
        if clock_ns is not None and (not math.isfinite(clock_ns) or clock_ns <= 0):
            raise ValueError("clock_ns must be finite and positive")
        area = area_by_block_um2 or {}
        if any(not math.isfinite(value) or value < 0 for value in area.values()):
            raise ValueError("block areas must be finite and nonnegative")
        cycles = sum(row.cycles for row in self.bench)
        instructions = sum(row.instructions for row in self.bench)
        return CpuProfile(
            cpi=cycles / instructions,
            cycles_by_stall=CyclesByStall(**{
                key: sum(getattr(row.cycles_by_stall, key) for row in self.bench)
                for key in ("busy", "fetch", "data")
            }),
            bench=self.bench, bench_time_us=cycles * clock_ns / 1000 if clock_ns is not None else None,
            clock_ns=clock_ns, area_by_block_um2=area, critical_path_blocks=critical_path_blocks,
            wait_cycles=self.wait_cycles,
        )


def parse_bench(payload: dict[str, Any], *, expected_names: tuple[str, ...] = BENCH_NAMES) -> BenchResult:
    result = BenchResult.model_validate(payload)
    names = [row.name for row in result.bench]
    complete = names == list(expected_names)
    valid = True
    for row in result.bench:
        stalls = row.cycles_by_stall
        measured = row.cycles > 0 and row.instructions > 0 and row.instructions <= row.cycles
        measured = measured and stalls.busy + stalls.fetch + stalls.data == row.cycles
        cpi = row.cycles / row.instructions if measured else None
        passed = measured and row.passed and row.self_check == 1
        row.cpi = cpi
        row.passed = passed
        valid = valid and passed
    if result.status == "pass" and not (complete and valid):
        result.status = "fail"
        result.message = "benchmark pass requires the complete ordered suite, valid counters and every self-check"
    return result
