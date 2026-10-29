"""Tests for the deterministic chip fabricability and machine check engine.

Covers:
- Rejection of physically impossible 1.8 nm silicon chips (the scenario reported in the convo)
- Optical diffraction / Rayleigh resolution limits of ASML High-NA EUV EXE:5200
- Quantum mechanical tunneling barriers and atomic unit cell limits in Silicon
- Electric field stress and dielectric breakdown (Vdd / Lg)
- Thermal power density dissipation limits (W/cm2)
- Carrier transit and digital frequency cutoffs
- Actionable rework recommendations for the agent/planner
- Extraction from text and integration with orchestrator feasibility gate
"""

import pytest

from verity.contracts import PlanBlock, Variant
from verity.core.pack import TaskPack
from verity.core.types import PlanOut
from verity.fabrication import (
    ASML_EXE_5200,
    ASML_NXE_3800E,
    BEST_CHIP_MAKING_DEVICE,
    NANGATE45_MACHINE,
    ChipSpec,
    check_chip_fabricability,
    extract_chip_spec_from_plan,
    extract_chip_spec_from_text,
    get_machine_spec,
    get_semiconductor_spec,
)
from verity.orchestrator.feasibility import check_plan


# ======================================================================
# 1. The Core Convo Problem: 1.8 nm Chip on Silicon
# ======================================================================

def test_1_8nm_silicon_rejected_with_exact_physics_and_machine_reasons() -> None:
    """The model previously generated a chip with passed status at 1.8 nanometers.

    The deterministic machine check must reject this with exact physical reasons:
    1. Below ASML EXE:5200 High-NA EUV optical resolution limit (single-exposure 8.0 nm, multi-patterning 5.0 nm)
    2. Below Silicon quantum tunneling limit (5.0 nm physical gate length)
    3. Below lattice stability (~3 unit cells of Si, lattice constant 0.543 nm)
    4. Provide clear rework advice clarifying commercial naming (e.g. Intel 18A Lg >= 12nm)
    """
    spec = ChipSpec(
        feature_size_nm=1.8,
        process_node_name="1.8nm",
        channel_material="silicon",
    )
    result = check_chip_fabricability(spec, machine=BEST_CHIP_MAKING_DEVICE)

    assert result.can_build is False
    assert result.status == "FAIL"
    assert len(result.reasons) >= 2

    joined_reasons = " ".join(result.reasons)
    # Check lithography limit mention
    assert "resolution limit" in joined_reasons.lower()
    assert "asml" in joined_reasons.lower() or "exe:5200" in joined_reasons.lower()

    # Check quantum tunneling limit mention
    assert "quantum mechanical tunneling" in joined_reasons.lower()
    assert "5.0" in joined_reasons  # 5.0 nm Silicon physical cutoff

    # Check lattice unit cell mention
    assert "0.543" in joined_reasons  # Silicon lattice constant

    # Check rework advice
    assert len(result.rework_recommendations) >= 1
    rework = " ".join(result.rework_recommendations)
    assert "intel 18a" in rework.lower() or "commercial" in rework.lower()
    assert "12" in rework  # Lg >= 12nm guidance


def test_sub_5nm_silicon_tunneling_rejection() -> None:
    """A 3.0 nm physical feature size on Silicon must be rejected due to quantum tunneling."""
    spec = ChipSpec(feature_size_nm=3.0, channel_material="silicon")
    result = check_chip_fabricability(spec, machine=BEST_CHIP_MAKING_DEVICE)

    assert result.can_build is False
    assert any("quantum mechanical tunneling" in r.lower() for r in result.reasons)


# ======================================================================
# 2. Machine Optical Rayleigh Limit Checks
# ======================================================================

def test_machine_rayleigh_limit_exe_5200() -> None:
    """ASML EXE:5200 has 0.55 NA, single-exposure CD = 8.0 nm, multi-patterning limit = 5.0 nm."""
    # 4.0 nm is below the multi-patterning limit of 5.0 nm
    spec = ChipSpec(feature_size_nm=4.0, channel_material="mos2")
    result = check_chip_fabricability(spec, machine=ASML_EXE_5200)

    assert result.can_build is False
    assert any("below the absolute physical resolution limit" in r for r in result.reasons)


def test_machine_rayleigh_limit_nxe_3800e() -> None:
    """ASML NXE:3800E (Low-NA EUV, 0.33 NA) has single exposure 13.0 nm and multi-patterning 7.0 nm."""
    # 6.0 nm is below NXE:3800E's multi-patterning limit of 7.0 nm
    spec = ChipSpec(feature_size_nm=6.0, channel_material="mos2")
    result = check_chip_fabricability(spec, machine=ASML_NXE_3800E)

    assert result.can_build is False
    assert any("NXE:3800E" in r for r in result.reasons)


# ======================================================================
# 3. Valid Designs Pass Deterministically
# ======================================================================

def test_valid_nangate45_design_passes() -> None:
    """A standard 45nm Silicon CMOS design on Nangate45 reference fab passes all checks."""
    spec = ChipSpec(
        feature_size_nm=45.0,
        process_node_name="Nangate45",
        channel_material="silicon",
        gate_dielectric="high_k",
        interconnect_metal="copper",
        cooling_method="air",
        supply_voltage_v=1.1,
        clock_frequency_ghz=0.2,  # 200 MHz
        power_w=0.05,  # 50 mW
        area_um2=2000.0,
        cell_count=400,
    )
    result = check_chip_fabricability(spec, machine=NANGATE45_MACHINE)

    assert result.can_build is True
    assert result.status == "PASS"
    assert len(result.reasons) == 0


def test_valid_sota_2nm_compliant_spec_passes() -> None:
    """A 2nm-class GAA nanosheet design with physical Lg=12nm, Vdd=0.7V, high-k on ASML EXE:5200 passes."""
    spec = ChipSpec(
        feature_size_nm=12.0,  # Realistic physical gate length for 2nm class
        process_node_name="TSMC N2 / Intel 18A class",
        channel_material="silicon",
        gate_dielectric="high_k",
        interconnect_metal="ruthenium",
        cooling_method="liquid",
        supply_voltage_v=0.7,
        clock_frequency_ghz=3.5,
        power_w=15.0,
        die_area_mm2=25.0,  # 25 mm2 -> 60 W/cm2, well within liquid cooling
        contacted_poly_pitch_nm=45.0,
        metal_pitch_nm=18.0,
    )
    result = check_chip_fabricability(spec, machine=ASML_EXE_5200)

    assert result.can_build is True
    assert result.status == "PASS"
    assert len(result.reasons) == 0


# ======================================================================
# 4. Electrical and Dielectric Breakdown (Vdd / Lg)
# ======================================================================

def test_electric_field_dielectric_breakdown() -> None:
    """Applying 1.8V across a 1.5nm gate produces 1.2 V/nm, exceeding dielectric breakdown."""
    spec = ChipSpec(
        feature_size_nm=1.5,
        supply_voltage_v=1.8,
        channel_material="mos2",
    )
    result = check_chip_fabricability(spec, machine=ASML_EXE_5200)

    assert result.can_build is False
    assert any("dielectric breakdown" in r.lower() for r in result.reasons)
    assert any("lower supply voltage" in rec.lower() for rec in result.rework_recommendations)


# ======================================================================
# 5. Thermal Power Density Limits
# ======================================================================

def test_thermal_power_density_exceeded() -> None:
    """100W dissipated on a 1 mm2 die gives 10,000 W/cm2, far exceeding air cooling (100 W/cm2)."""
    spec = ChipSpec(
        feature_size_nm=45.0,
        power_w=100.0,
        die_area_mm2=1.0,
        cooling_method="air",
    )
    result = check_chip_fabricability(spec)

    assert result.can_build is False
    assert any("thermal power density" in r.lower() for r in result.reasons)
    assert any("cooling" in rec.lower() for rec in result.rework_recommendations)


# ======================================================================
# 6. Frequency and Physical Velocity Cutoff
# ======================================================================

def test_unrealistic_clock_frequency_rejected() -> None:
    """10 GHz digital CMOS clock frequency exceeds room-temp silicon cutoff (5.5 GHz)."""
    spec = ChipSpec(
        feature_size_nm=45.0,
        clock_frequency_ghz=10.0,
        channel_material="silicon",
    )
    result = check_chip_fabricability(spec)

    assert result.can_build is False
    assert any("clock frequency" in r.lower() for r in result.reasons)
    assert any("5.5" in r for r in result.reasons)


# ======================================================================
# 7. Reticle Field Size Limit
# ======================================================================

def test_reticle_field_size_limit_on_high_na() -> None:
    """ASML High-NA EUV has an anamorphic exposure field of 429 mm2. A 500 mm2 monolithic die fails."""
    spec = ChipSpec(
        feature_size_nm=12.0,
        die_area_mm2=500.0,
    )
    result = check_chip_fabricability(spec, machine=ASML_EXE_5200)

    assert result.can_build is False
    assert any("exposure field" in r.lower() for r in result.reasons)


# ======================================================================
# 8. Standard Cell Layout Density Limit
# ======================================================================

def test_impossible_cell_density_rejected() -> None:
    """50,000 cells in 10 um2 implies 0.0002 um2 per cell, which is geometrically impossible."""
    spec = ChipSpec(
        feature_size_nm=12.0,
        area_um2=10.0,
        cell_count=50_000,
    )
    result = check_chip_fabricability(spec, machine=ASML_EXE_5200)

    assert result.can_build is False
    assert any("cell area" in r.lower() for r in result.reasons)


# ======================================================================
# 9. Text Extraction
# ======================================================================

def test_extract_chip_spec_from_text() -> None:
    text = (
        "We are designing an accelerator targeting 1.8nm process node with 0.75V supply voltage, "
        "operating at 2.5 GHz on Silicon with liquid cooling."
    )
    spec = extract_chip_spec_from_text(text)

    assert spec.feature_size_nm == 1.8
    assert spec.supply_voltage_v == 0.75
    assert spec.clock_frequency_ghz == 2.5
    assert spec.channel_material == "silicon"
    assert spec.cooling_method == "liquid"


# ======================================================================
# 10. Orchestrator Feasibility Gate Integration
# ======================================================================

def test_feasibility_gate_rejects_plan_proposing_1_8nm(settings, mac4: TaskPack) -> None:
    """When a plan proposes a 1.8nm chip size / process node, check_plan must reject it deterministically."""
    bad_plan = PlanOut(
        summary="A high performance design built with a 1.8nm chip size.",
        blocks=[
            PlanBlock(id="blk-in", name="Input registers", reason="Capture inputs at 1.8nm node."),
            PlanBlock(id="blk-mult", name="Multiplier", reason="MAC units."),
            PlanBlock(id="blk-acc", name="Accumulator", reason="Summing logic."),
        ],
        latency_cycles=mac4.latency_cycles,
        estimated_lines=120,
    )
    reasons = check_plan(bad_plan, mac4, settings)

    assert len(reasons) > 0
    joined = " ".join(reasons)
    assert "1.8" in joined or "resolution limit" in joined or "quantum mechanical tunneling" in joined


def test_feasibility_gate_accepts_valid_plan(settings, mac4: TaskPack) -> None:
    """Standard compliant plan continues to pass cleanly."""
    valid_plan = PlanOut(
        summary="A standard MAC array.",
        blocks=[
            PlanBlock(id="blk-in", name="Input registers", reason="Capture inputs."),
            PlanBlock(id="blk-mult", name="Multiplier", reason="MAC units."),
            PlanBlock(id="blk-acc", name="Accumulator", reason="Summing logic."),
        ],
        latency_cycles=mac4.latency_cycles,
        estimated_lines=120,
    )
    assert check_plan(valid_plan, mac4, settings) == []
