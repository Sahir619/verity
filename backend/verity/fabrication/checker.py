"""Deterministic chip fabricability engine.

Evaluates whether a chip design can physically be manufactured given:
1. Lithography machine specifications (e.g. ASML TWINSCAN EXE:5200 High-NA EUV)
2. Materials science properties (Silicon, MoS2, high-k dielectrics, Cu/Ru interconnects)
3. Operating conditions (voltage, frequency, power density, thermal cooling)

Completely deterministic: no LLM calls, zero non-determinism.
Returns exact physical failure reasons and actionable rework recommendations for agents.
"""

from dataclasses import dataclass, field
import math
import re
from typing import Any, Literal

from .machine_specs import BEST_CHIP_MAKING_DEVICE, MachineSpec, get_machine_spec
from .material_specs import (
    SILICON,
    DielectricMaterial,
    InterconnectMaterial,
    SemiconductorMaterial,
    get_cooling_spec,
    get_dielectric_spec,
    get_interconnect_spec,
    get_semiconductor_spec,
)


@dataclass(slots=True)
class ChipSpec:
    """Requested or estimated physical specifications of a chip design."""

    feature_size_nm: float | None = None  # physical critical dimension / gate length Lg
    process_node_name: str | None = None  # e.g. "1.8nm", "Intel 18A", "TSMC N2", "Nangate45"
    channel_material: str = "silicon"
    gate_dielectric: str = "high_k"
    interconnect_metal: str = "copper"
    cooling_method: str = "air"
    supply_voltage_v: float | None = None
    clock_frequency_ghz: float | None = None
    power_w: float | None = None
    area_um2: float | None = None
    die_area_mm2: float | None = None
    cell_count: int | None = None
    contacted_poly_pitch_nm: float | None = None
    metal_pitch_nm: float | None = None
    gate_dielectric_thickness_nm: float | None = None
    current_density_ma_cm2: float | None = None


@dataclass(slots=True)
class FabricabilityResult:
    """Outcome of the deterministic machine check."""

    can_build: bool
    status: Literal["PASS", "FAIL"]
    reasons: list[str] = field(default_factory=list)
    rework_recommendations: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    machine: str = ""
    materials: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_build": self.can_build,
            "status": self.status,
            "reasons": list(self.reasons),
            "rework_recommendations": list(self.rework_recommendations),
            "diagnostics": dict(self.diagnostics),
            "machine": self.machine,
            "materials": dict(self.materials),
        }


# Pattern to detect mentions of nanometer nodes in free-text strings
_NODE_REGEX = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:nm|nanometer|nanometers|a|angstrom)\b", re.IGNORECASE)


def check_chip_fabricability(
    spec: ChipSpec,
    machine: MachineSpec | None = None,
) -> FabricabilityResult:
    """Deterministically check if a chip can physically be manufactured.

    Evaluates:
    - Lithography optical diffraction / Rayleigh limits against the machine
    - Quantum mechanical tunneling barrier and atomic lattice limits on Silicon
    - Gate oxide dielectric thickness and quantum punchthrough
    - Electric field breakdown stress (Vdd / Lg)
    - Electromigration limits on metal interconnects
    - Thermal power density (W/cm2) dissipation vs cooling solution
    - Room-temperature carrier transit cutoff frequency
    - Reticle exposure field size limits
    - Minimum cell area and transistor density feasibility
    """
    if machine is None:
        machine = BEST_CHIP_MAKING_DEVICE

    semiconductor = get_semiconductor_spec(spec.channel_material)
    dielectric = get_dielectric_spec(spec.gate_dielectric)
    interconnect = get_interconnect_spec(spec.interconnect_metal)
    cooling = get_cooling_spec(spec.cooling_method)

    reasons: list[str] = []
    rework: list[str] = []
    diag: dict[str, Any] = {
        "machine_name": machine.name,
        "machine_technology": machine.technology,
        "machine_resolution_limit_nm": machine.single_exposure_resolution_nm,
        "channel_material": semiconductor.name,
        "gate_dielectric": dielectric.name,
        "interconnect": interconnect.name,
        "cooling": cooling.name,
    }

    # Normalize feature size from node name if explicit feature_size_nm is absent
    effective_feature_nm = spec.feature_size_nm
    is_marketing_name_ambiguity = False

    if effective_feature_nm is None and spec.process_node_name:
        match = _NODE_REGEX.search(spec.process_node_name)
        if match:
            parsed_val = float(match.group(1))
            # e.g. "18A" -> 1.8nm, "1.8nm" -> 1.8nm
            if "a" in match.group(0).lower() and parsed_val > 10:
                parsed_val = parsed_val / 10.0
            effective_feature_nm = parsed_val
            is_marketing_name_ambiguity = True

    # ------------------------------------------------------------------
    # 1. Feature Size & Machine Optical Resolution Limit
    # ------------------------------------------------------------------
    if effective_feature_nm is not None:
        diag["feature_size_nm"] = effective_feature_nm

        # Hard lithography limit
        min_machine_feature = machine.multi_patterning_min_feature_nm
        single_exp_limit = machine.single_exposure_resolution_nm

        if effective_feature_nm < min_machine_feature:
            reasons.append(
                f"Feature size {effective_feature_nm:.2f} nm is below the absolute physical resolution limit of "
                f"the best lithography machine ({machine.name}: single-exposure resolution = {single_exp_limit:.1f} nm, "
                f"multi-patterning limit = {min_machine_feature:.1f} nm)."
            )
            rework.append(
                f"Increase physical feature size to at least {single_exp_limit:.1f} nm for single-exposure High-NA EUV, "
                f"or >= {min_machine_feature:.1f} nm with GAA nanosheet multi-patterning."
            )
        elif effective_feature_nm < single_exp_limit:
            diag["lithography_mode"] = "multi_patterning_required"

        # ------------------------------------------------------------------
        # 2. Materials Science: Silicon Quantum Mechanical Tunneling Barrier
        # ------------------------------------------------------------------
        if semiconductor.id == "silicon":
            if effective_feature_nm < semiconductor.direct_tunneling_limit_nm:
                reasons.append(
                    f"Physical feature size {effective_feature_nm:.2f} nm on Silicon violates fundamental quantum mechanical "
                    f"tunneling limits (minimum physical gate length for Silicon is {semiconductor.direct_tunneling_limit_nm:.1f} nm). "
                    f"Below 5.0 nm, direct source-to-drain wave-function tunneling causes severe off-state leakage "
                    f"(I_off > 100 nA/um) and subthreshold swing degrades past 120 mV/decade, preventing binary transistor switching. "
                    f"Silicon unit cell lattice constant is {semiconductor.lattice_constant_nm:.4f} nm, meaning {effective_feature_nm:.2f} nm "
                    f"spans only ~3 unit cells, resulting in catastrophic statistical dopant fluctuation."
                )

                if effective_feature_nm <= 2.0:
                    rework.append(
                        "Disambiguate commercial marketing node naming: Commercial nodes labeled '1.8nm' (Intel 18A) or '1.6nm' (TSMC A16) "
                        "do NOT have 1.8 nm physical gates; they feature physical gate lengths Lg >= 12 nm, nanosheet thickness ~5 nm, "
                        "and contacted poly pitch CPP >= 45 nm. Specify physical gate length Lg >= 12 nm with GAA nanosheets, "
                        "or adjust the process node target to a commercially verifiable PDK (such as 45nm or 130nm)."
                    )
                else:
                    rework.append(
                        f"Increase Silicon physical gate length to >= {semiconductor.direct_tunneling_limit_nm:.1f} nm, "
                        f"or switch to an experimental 2D TMD material (e.g. MoS2) if exploring laboratory sub-2nm prototypes."
                    )
        else:
            if effective_feature_nm < semiconductor.direct_tunneling_limit_nm:
                reasons.append(
                    f"Physical feature size {effective_feature_nm:.2f} nm for {semiconductor.name} violates its direct "
                    f"tunneling limit ({semiconductor.direct_tunneling_limit_nm:.1f} nm)."
                )
                rework.append(
                    f"Increase feature size to at least {semiconductor.direct_tunneling_limit_nm:.1f} nm for {semiconductor.name}."
                )

            if semiconductor.commercial_readiness == "laboratory_prototype":
                reasons.append(
                    f"Material {semiconductor.name} is a laboratory prototype (TRL 3-4) and is not available in 300mm "
                    f"high-volume manufacturing fabs ({machine.name} operates on 300mm silicon wafers)."
                )
                rework.append("Target commercial Silicon CMOS for synthesizable and fabricable designs.")

    # ------------------------------------------------------------------
    # 3. Contacted Poly Pitch (CPP) and Metal Pitch (MMP)
    # ------------------------------------------------------------------
    if spec.contacted_poly_pitch_nm is not None:
        diag["contacted_poly_pitch_nm"] = spec.contacted_poly_pitch_nm
        if spec.contacted_poly_pitch_nm < machine.contacted_poly_pitch_nm:
            reasons.append(
                f"Contacted poly pitch {spec.contacted_poly_pitch_nm:.1f} nm is below {machine.name} minimum "
                f"CPP limit of {machine.contacted_poly_pitch_nm:.1f} nm."
            )
            rework.append(f"Relax contacted poly pitch to >= {machine.contacted_poly_pitch_nm:.1f} nm.")

    if spec.metal_pitch_nm is not None:
        diag["metal_pitch_nm"] = spec.metal_pitch_nm
        if spec.metal_pitch_nm < machine.min_metal_pitch_nm:
            reasons.append(
                f"Minimum metal pitch {spec.metal_pitch_nm:.1f} nm is below {machine.name} minimum "
                f"metal pitch limit of {machine.min_metal_pitch_nm:.1f} nm."
            )
            rework.append(f"Increase interconnect metal pitch to >= {machine.min_metal_pitch_nm:.1f} nm.")

    # ------------------------------------------------------------------
    # 4. Gate Dielectric Thickness & Tunneling Punchthrough
    # ------------------------------------------------------------------
    if spec.gate_dielectric_thickness_nm is not None:
        diag["gate_dielectric_thickness_nm"] = spec.gate_dielectric_thickness_nm
        if spec.gate_dielectric_thickness_nm < dielectric.min_physical_thickness_nm:
            reasons.append(
                f"Gate dielectric thickness {spec.gate_dielectric_thickness_nm:.2f} nm for {dielectric.name} is below "
                f"the physical quantum tunneling threshold ({dielectric.min_physical_thickness_nm:.2f} nm). "
                f"Direct quantum tunneling causes excessive standby leakage (> 1 A/cm2)."
            )
            rework.append(
                f"Increase dielectric physical thickness to >= {dielectric.min_physical_thickness_nm:.2f} nm "
                f"(or use a high-k material with larger dielectric constant k)."
            )

    # ------------------------------------------------------------------
    # 5. Electric Field Stress & Dielectric Breakdown (Vdd / Lg)
    # ------------------------------------------------------------------
    if spec.supply_voltage_v is not None and effective_feature_nm is not None and effective_feature_nm > 0:
        electric_field_v_nm = spec.supply_voltage_v / effective_feature_nm
        diag["electric_field_v_nm"] = round(electric_field_v_nm, 3)

        if electric_field_v_nm > dielectric.breakdown_field_v_nm:
            reasons.append(
                f"Electric field ({electric_field_v_nm:.2f} V/nm) from supply voltage {spec.supply_voltage_v:.2f} V across "
                f"{effective_feature_nm:.2f} nm exceeds the dielectric breakdown threshold of {dielectric.name} "
                f"({dielectric.breakdown_field_v_nm:.2f} V/nm). Immediate punchthrough and dielectric failure will occur."
            )
            max_safe_v = round(dielectric.breakdown_field_v_nm * effective_feature_nm * 0.75, 2)
            rework.append(f"Lower supply voltage to <= {max_safe_v} V (or increase gate length).")

    # ------------------------------------------------------------------
    # 6. Interconnect Electromigration Limits
    # ------------------------------------------------------------------
    if spec.current_density_ma_cm2 is not None:
        diag["current_density_ma_cm2"] = spec.current_density_ma_cm2
        if spec.current_density_ma_cm2 > interconnect.max_current_density_ma_cm2:
            reasons.append(
                f"Current density ({spec.current_density_ma_cm2:.2f} MA/cm2) exceeds the electromigration threshold "
                f"of {interconnect.name} ({interconnect.max_current_density_ma_cm2:.2f} MA/cm2). Wires will void and disconnect."
            )
            rework.append(
                f"Widen metal interconnect lines, lower peak switching current, or upgrade interconnect metallization "
                f"to Ruthenium/Molybdenum (limits up to 5.0 MA/cm2)."
            )

    # ------------------------------------------------------------------
    # 7. Thermal Power Density & Thermal Runaway (P / Area)
    # ------------------------------------------------------------------
    total_area_cm2: float | None = None
    if spec.die_area_mm2 is not None and spec.die_area_mm2 > 0:
        total_area_cm2 = spec.die_area_mm2 * 0.01  # 1 mm2 = 0.01 cm2
    elif spec.area_um2 is not None and spec.area_um2 > 0:
        # Synthesized cell area for an IP sub-block sits on a packaged die with minimum
        # sawing/handling footprint of at least 0.25 mm2 (250,000 um2) with bond pads.
        effective_um2 = max(spec.area_um2, 250_000.0)
        total_area_cm2 = effective_um2 * 1e-8  # 1 um2 = 1e-8 cm2

    if spec.power_w is not None and total_area_cm2 is not None and total_area_cm2 > 0:
        power_density_w_cm2 = spec.power_w / total_area_cm2
        diag["power_density_w_cm2"] = round(power_density_w_cm2, 2)

        if power_density_w_cm2 > cooling.max_power_density_w_cm2:
            reasons.append(
                f"Thermal power density ({power_density_w_cm2:.1f} W/cm2) exceeds the maximum dissipation capacity of "
                f"{cooling.name} ({cooling.max_power_density_w_cm2:.1f} W/cm2). Rapid thermal runaway and silicon junction "
                f"damage will occur."
            )
            rework.append(
                f"Reduce dynamic power dissipation, increase die footprint, or upgrade cooling solution "
                f"(e.g., Liquid Cold Plate up to 200 W/cm2 or Microchannel Immersion up to 350 W/cm2)."
            )

    # ------------------------------------------------------------------
    # 8. Digital Clock Frequency vs Physical Carrier Transit Delay
    # ------------------------------------------------------------------
    if spec.clock_frequency_ghz is not None:
        diag["clock_frequency_ghz"] = spec.clock_frequency_ghz
        if spec.clock_frequency_ghz > semiconductor.max_frequency_ghz:
            reasons.append(
                f"Target clock frequency {spec.clock_frequency_ghz:.2f} GHz exceeds the practical room-temperature "
                f"digital switching cutoff for {semiconductor.name} ({semiconductor.max_frequency_ghz:.1f} GHz). "
                f"Carrier transit saturation and RC parasitics prevent reliable synchronous timing."
            )
            rework.append(
                f"Reduce clock frequency to <= {semiconductor.max_frequency_ghz:.1f} GHz or add pipelining registers "
                f"to shorten combinational path length."
            )

    # ------------------------------------------------------------------
    # 9. Reticle Field Exposure Area (Max Die Dimension)
    # ------------------------------------------------------------------
    if spec.die_area_mm2 is not None:
        diag["die_area_mm2"] = spec.die_area_mm2
        if spec.die_area_mm2 > machine.max_die_area_mm2:
            reasons.append(
                f"Die area ({spec.die_area_mm2:.1f} mm2) exceeds the maximum single exposure field of {machine.name} "
                f"({machine.max_die_area_mm2:.1f} mm2, {machine.max_reticle_field_w_mm:.1f} x {machine.max_reticle_field_h_mm:.1f} mm). "
                f"Monolithic die cannot be patterned without multi-mask reticle stitching."
            )
            rework.append(
                f"Partition the design into modular chiplets or reduce monolithic die size below {machine.max_die_area_mm2:.1f} mm2."
            )

    # ------------------------------------------------------------------
    # 10. Standard Cell Layout Geometries & Transistor Density Upper Bound
    # ------------------------------------------------------------------
    if spec.cell_count is not None and spec.area_um2 is not None and spec.cell_count > 0 and spec.area_um2 > 0:
        area_per_cell_um2 = spec.area_um2 / spec.cell_count
        diag["area_per_cell_um2"] = round(area_per_cell_um2, 5)

        # Minimum physical standard cell area for 2nm SOTA (TSMC N2 / Intel 18A):
        # Even at 2nm, a minimum standard cell requires ~0.015 - 0.025 um2.
        # Below 0.005 um2 per cell is physically impossible.
        if area_per_cell_um2 < 0.005:
            reasons.append(
                f"Standard cell area ({area_per_cell_um2:.5f} um2/cell for {spec.cell_count} cells) violates minimum "
                f"physical layout rules. Even on state-of-the-art 2nm nodes, a standard logic cell requires at least "
                f"0.02 um2 for contacted poly pitch, fin/nanosheet tracks, and metal-1 routing."
            )
            rework.append(
                f"Re-synthesize with realistic standard cell libraries (e.g. Nangate45 cell area ~ 1.0 um2/cell, "
                f"or SOTA 2nm cell area ~ 0.03 um2/cell)."
            )

    can_build = len(reasons) == 0
    status: Literal["PASS", "FAIL"] = "PASS" if can_build else "FAIL"

    return FabricabilityResult(
        can_build=can_build,
        status=status,
        reasons=reasons,
        rework_recommendations=rework,
        diagnostics=diag,
        machine=machine.name,
        materials={
            "semiconductor": semiconductor.name,
            "gate_dielectric": dielectric.name,
            "interconnect": interconnect.name,
            "cooling": cooling.name,
        },
    )
