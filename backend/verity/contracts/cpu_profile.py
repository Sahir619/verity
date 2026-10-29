"""Measured CPU profile. Python names serialize as camelCase."""

from pydantic import Field

from .base import WireModel


class CyclesByStall(WireModel):
    busy: int = Field(ge=0, strict=True)
    fetch: int = Field(ge=0, strict=True)
    data: int = Field(ge=0, strict=True)


class BenchRow(WireModel):
    name: str
    instructions: int = Field(ge=0, strict=True)
    cycles: int = Field(ge=0, strict=True)
    cpi: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    passed: bool = Field(strict=True)
    cycles_by_stall: CyclesByStall
    self_check: int | None = Field(default=None, strict=True)
    message: str | None = None


class CpuProfile(WireModel):
    cpi: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cycles_by_stall: CyclesByStall
    bench: list[BenchRow]
    bench_time_us: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    area_by_block_um2: dict[str, float] = Field(default_factory=dict)
    critical_path_blocks: list[str] | None = None
    clock_ns: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Memory wait cycles the benchmarks ran with: 0 fast memory, 3 flash-like memory.
    wait_cycles: int = Field(default=0, ge=0, strict=True)
