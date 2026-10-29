"""Fixed upgrade catalog (phases A to C). Sources explain mechanisms, not measured gains on Verity.

Every percentage below is our explicit planning estimate, not a source's benchmark.
Predictions compare with the supplied CPU profile. No model supplies a number.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts.cpu import Prediction, UpgradeSource


class BenchRow(Protocol):
    instructions: int
    passed: bool


class Profile(Protocol):
    cpi: float | None
    cycles_by_stall: Any  # CyclesByStall or a plain dict with busy, fetch and data
    bench: list[BenchRow]
    area_by_block_um2: dict[str, float]
    clock_ns: float | None


def stall_counts(profile: Profile) -> dict[str, int]:
    """The busy, fetch and data cycle totals of a profile as a dict. Empty when the profile has none."""
    counts = profile.cycles_by_stall
    if isinstance(counts, dict):
        return counts
    return {k: getattr(counts, k) for k in ("busy", "fetch", "data") if hasattr(counts, k)}


# cache_16 planning assumptions. A hit answers in the request's own cycle, so each hit removes that access's wait.
# Taken from the reference cores at wait 3 (2026-10-28), not from the design being judged: fetch waits fell 11% on
# crc32 and 92% on matmult-int, and data waits did not fall (the loop's own fetches keep refilling the 16 lines).
FLASH_WAIT_CYCLES = 3  # the flash-like memory profile
CACHE_FETCH_HIT = 0.5
CACHE_DATA_HIT = 0.0


@dataclass(frozen=True)
class Upgrade:
    id: str
    label: str
    targets: tuple[str, ...]
    needs: tuple[str, ...]
    blocks: tuple[str, ...]
    contract_parts: tuple[str, ...]
    keywords: tuple[str, ...]
    source_title: str
    source_url: str
    area_delta_pct: float
    clock_delta_pct: float
    formula: str
    assumption: str
    buildable: bool = False

    def predict(self, profile: Profile, flash_profile: Profile | None = None) -> Prediction:
        """C=measured CPI; I=passed benchmark instructions; B,F,D=stall cycles / I.

        pipeline_2: C'=min(C,2+F+D), with one unmeasured hazard cycle per instruction.
        fetch_overlap: C'=max(1,C-min(F,max(0,B-1))).
        decode_merge: C'=max(1,C-min(1,max(0,B-1))).
        cache_16: uses the flash-like profile (wait 3), where C, B, F and D are measured; with fast memory a cache
        cannot help, so the fast profile alone gives no estimate. C'=C-0.5*F: half of the fetch waits disappear and the
        data waits stay (assumed hit rates, from the reference cores, not from this design). Its CPI numbers are relative to
        the flash-like CPI (basis_wait_cycles 3). Other entries: C'=C.
        A'=A*(1+area_delta_pct/100); T'=T*(1+clock_delta_pct/100), A and T from the fast profile.
        Runtime ratio=(C'/C)*(T'/T). Power proxy=(A'/A)/(T'/T), fixed V/activity.
        Missing counters leave CPI unchanged; missing CPI, area or clock stays null.
        """
        base = flash_profile if self.id == "cache_16" else profile
        cpi = base.cpi if base is not None else None
        instructions = sum(row.instructions for row in base.bench if row.passed) if base is not None else 0
        counts = stall_counts(base) if base is not None else {}
        stalls_available = instructions > 0 and all(k in counts for k in ("busy", "fetch", "data"))
        predicted = cpi
        if cpi is not None and cpi > 0 and stalls_available:
            busy, fetch, data = (counts[k] / instructions for k in ("busy", "fetch", "data"))
            if self.id == "pipeline_2":
                predicted = min(cpi, 2 + fetch + data)
            elif self.id == "fetch_overlap":
                predicted = max(1, cpi - min(fetch, max(0, busy - 1)))
            elif self.id == "decode_merge":
                predicted = max(1, cpi - min(1, max(0, busy - 1)))
            elif self.id == "cache_16":
                predicted = cpi - CACHE_FETCH_HIT * fetch - CACHE_DATA_HIT * data
        area_ratio, clock_ratio = 1 + self.area_delta_pct / 100, 1 + self.clock_delta_pct / 100
        area = sum(profile.area_by_block_um2.values()) if profile.area_by_block_um2 else None
        return Prediction(
            cpi=predicted,
            cpi_delta=predicted - cpi if predicted is not None and cpi is not None else None,
            area_delta_pct=self.area_delta_pct, clock_delta_pct=self.clock_delta_pct,
            area_um2=area * area_ratio if area is not None else None,
            clock_ns=profile.clock_ns * clock_ratio if profile.clock_ns is not None else None,
            time_ratio=predicted / cpi * clock_ratio if predicted is not None and cpi and profile.clock_ns else None,
            power_ratio=area_ratio / clock_ratio, formula=self.formula,
            assumptions=self.assumption + " C is measured CPI; B, F and D are busy, fetch and data cycles per "
            "passed benchmark instruction; A is total block area; T is clock period. "
            "All area and clock deltas are planning estimates, not measured gains. "
            "Power is an area/frequency proxy at fixed voltage and activity, not measured mW."
            + (" Missing stall counters: CPI is unchanged." if not stalls_available else ""),
            basis_wait_cycles=FLASH_WAIT_CYCLES if self.id == "cache_16" else 0,
        )

    def source(self) -> UpgradeSource:
        return UpgradeSource(title=self.source_title, url=self.source_url)


UPGRADES = (
    # Opened: https://ibex-core.readthedocs.io/en/latest/03_reference/pipeline_details.html
    Upgrade("pipeline_2", "Two-stage pipeline", ("cpi", "cycles_by_stall.busy"), ("rvfi",),
            ("rv32i_pctl", "rv32i_pipe2_core"), ("1", "4", "10.1", "10.2", "11"), ("pipeline", "2-stage", "two-stage"),
            "Ibex pipeline details", "https://ibex-core.readthedocs.io/en/latest/03_reference/pipeline_details.html",
            15, 10, "C'=min(C,2+F+D); A'=1.15A; T'=1.10T",
            "Fetch | execute; keeps measured fetch/data waits. Adds one cycle per instruction for unknown hazards; "
            "instruction mix and single-port memory contention are not resolved by this estimate.", buildable=True),
    # Opened: https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/memory-types.html
    Upgrade("cache_16", "16-word cache", ("cycles_by_stall.fetch", "cycles_by_stall.data"), ("flash_profile",),
            ("rv32i_cache", "rv32i_core"), ("4", "12.2", "12.3", "12.5"), ("cache", "flash"),
            "ESP-IDF memory types: ESP32-C3 flash is accessed through a cache",
            "https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/memory-types.html",
            55, 0, "C'=C-0.5*F (C, F from the flash-like profile); A'=1.55A; T'=T",
            "Direct mapped, 16 one-word lines, read allocate, write through. Assumes half of the fetch waits become "
            "hits and no data waits do: on the reference cores fetch waits fell 11% (crc32) and 92% (matmult-int) "
            "and data waits did not fall. Area +55% and an unchanged clock follow the reference cores (+53%, clock "
            "within 2%); for this design they are planning estimates. With fast memory (wait 0) a cache cannot "
            "help.", buildable=True),
    # Opened: https://raw.githubusercontent.com/YosysHQ/picorv32/main/README.md
    Upgrade("fetch_overlap", "Overlap instruction fetch", ("cycles_by_stall.fetch",), ("memory_protocol",),
            ("rv32i_ctl", "rv32i_core"), ("4",), ("look-ahead", "lookahead", "prefetch"),
            "PicoRV32 look-ahead memory interface", "https://raw.githubusercontent.com/YosysHQ/picorv32/main/README.md",
            5, 5, "C'=max(1,C-min(F,max(0,B-1))); A'=1.05A; T'=1.05T",
            "Assumes fetch waits can overlap busy cycles beyond one; data accesses still serialize. "
            "Look-ahead may worsen timing closure."),
    # Opened: https://shop.elsevier.com/books/computer-organization-and-design-risc-v-edition/patterson/978-0-12-820331-6
    Upgrade("decode_merge", "Merge decode into execute", ("cycles_by_stall.busy",), ("control_contract",),
            ("rv32i_dec", "rv32i_ctl", "rv32i_core"), ("3", "4"), ("decode", "multicycle", "multi-cycle"),
            "Patterson and Hennessy: The RISC-V Processor",
            "https://shop.elsevier.com/books/computer-organization-and-design-risc-v-edition/patterson/978-0-12-820331-6",
            -3, 10, "C'=max(1,C-min(1,max(0,B-1))); A'=0.97A; T'=1.10T",
            "Removes at most one busy cycle per instruction; combined decode/execute may lengthen the clock."),
    # Opened: https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32e.html
    Upgrade("rv32e", "RV32E reduced register file", ("area_by_block_um2.rv32i_rf",), ("new_isa_key", "recompile"),
            ("rv32i_rf", "rv32i_dec", "rv32i_core"), ("1", "3", "4", "10.3"), ("rv32e", "16 registers"),
            "RISC-V RV32E specification", "https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32e.html",
            -25, 0, "C'=C; A'=0.75A; T'=T",
            "The specification reports about 25% area savings on small cores excluding memories. "
            "CPI is held constant; extra spills and changed instruction counts require recompilation and new benchmarks."),
    # Opened: https://docs.riscv.org/reference/isa/v20260120/unpriv/m-st-ext.html
    Upgrade("ext_m", "Integer multiply and divide", ("bench_time_us",), ("new_isa_key", "recompile"),
            ("rv32i_alu", "rv32i_dec", "rv32i_ctl", "rv32i_core"), ("1", "2", "3", "4", "10.3"),
            ("multiply", "multiplication", "division"), "RISC-V M extension specification",
            "https://docs.riscv.org/reference/isa/v20260120/unpriv/m-st-ext.html",
            30, 10, "C'=C; A'=1.30A; T'=1.10T",
            "No software multiply/divide mix is measured, so no runtime savings are claimed. "
            "Hardware operation latency and recompiled instruction counts remain unknown."),
    # Opened: https://pages.hmc.edu/harris/ddca/ddcarv.html
    Upgrade("rf_banked_read", "Bank register-file reads", ("area_by_block_um2.rv32i_rf", "critical_path_blocks"),
            ("register_file_equivalence",), ("rv32i_rf",), ("3",), ("register file", "bank", "read mux"),
            "Harris and Harris: Digital Design and Computer Architecture, RISC-V",
            "https://pages.hmc.edu/harris/ddca/ddcarv.html",
            -5, -5, "C'=C; A'=0.95A; T'=0.95T",
            "Hierarchical read selection preserves both asynchronous ports; savings depend on mapping. "
            "The textbook motivates the structure, not these assumed percentages."),
)

CATALOG = {upgrade.id: upgrade for upgrade in UPGRADES}
