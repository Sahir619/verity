"""An upgrade job against its parent's CPU v1. Pure code: every number comes from the two measured profiles."""

from ..contracts.cpu import BenchDelta, CpuComparison, ProfileComparison
from ..contracts.cpu_profile import CpuProfile


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100


def _area(profile: CpuProfile | None) -> float | None:
    return sum(profile.area_by_block_um2.values()) if profile is not None and profile.area_by_block_um2 else None


def compare_mode(wait_cycles: int, parent: CpuProfile | None, child: CpuProfile | None) -> ProfileComparison | None:
    """None when neither side was measured in this memory mode."""
    if parent is None and child is None:
        return None
    mine = {row.name: row for row in child.bench} if child is not None else {}
    theirs = {row.name: row for row in parent.bench} if parent is not None else {}
    rows = []
    for name in [*theirs, *(n for n in mine if n not in theirs)]:
        old, new = theirs.get(name), mine.get(name)
        rows.append(BenchDelta(
            name=name,
            parent_cycles=old.cycles if old else None, cycles=new.cycles if new else None,
            cycles_delta_pct=_pct(new.cycles if new else None, old.cycles if old else None),
            parent_cpi=old.cpi if old else None, cpi=new.cpi if new else None,
        ))
    old_time = parent.bench_time_us if parent is not None else None
    new_time = child.bench_time_us if child is not None else None
    return ProfileComparison(
        wait_cycles=wait_cycles, parent=parent, child=child,
        cpi_delta_pct=_pct(child.cpi if child else None, parent.cpi if parent else None),
        bench_time_ratio=new_time / old_time if new_time is not None and old_time else None,
        bench=rows,
    )


def compare_profiles(
    parent: CpuProfile | None, parent_flash: CpuProfile | None,
    child: CpuProfile | None, child_flash: CpuProfile | None,
) -> CpuComparison:
    """Area and clock come from the fast profiles (the same block winners serve both memory modes). The flash
    comparison needs both sides: a core with no flash-like profile has nothing to put next to the parent's."""
    old_area, new_area = _area(parent), _area(child)
    old_clock = parent.clock_ns if parent is not None else None
    new_clock = child.clock_ns if child is not None else None
    both_flash = parent_flash is not None and child_flash is not None
    return CpuComparison(
        parent_area_um2=old_area, area_um2=new_area, area_delta_pct=_pct(new_area, old_area),
        parent_clock_ns=old_clock, clock_ns=new_clock, clock_delta_pct=_pct(new_clock, old_clock),
        fast=compare_mode(0, parent, child),
        flash=compare_mode(3, parent_flash, child_flash) if both_flash else None,
    )
