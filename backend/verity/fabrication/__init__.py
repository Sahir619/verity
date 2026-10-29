"""Verity deterministic chip fabricability and machine check engine.

Provides deterministic physical checks on chip realizability based on:
- SOTA lithography machines (e.g. ASML High-NA EUV EXE:5200)
- Semiconductor materials science (quantum tunneling, lattice limits, breakdown fields)
- Operational limits (voltage, frequency, power density, thermal cooling)
"""

from .checker import ChipSpec, FabricabilityResult, check_chip_fabricability
from .extract import extract_chip_spec_from_plan, extract_chip_spec_from_text
from .machine_specs import (
    ASML_EXE_5000,
    ASML_EXE_5200,
    ASML_NXE_3800E,
    ASML_NXT_2100I,
    BEST_CHIP_MAKING_DEVICE,
    CANON_NIL_1200,
    NANGATE45_MACHINE,
    MachineSpec,
    SOTA_MACHINES,
    get_machine_spec,
)
from .material_specs import (
    AIR_COOLED,
    ALUMINUM,
    CARBON_NANOTUBES,
    COPPER,
    CRYOGENIC,
    GALLIUM_ARSENIDE,
    GALLIUM_NITRIDE,
    HIGH_K_HFO2,
    LIQUID_COLD_PLATE,
    MICROCHANNEL_IMMERSION,
    MOS2_2D,
    RUTHENIUM_MOLY,
    SILICON,
    SILICON_DIOXIDE,
    SILICON_GERMANIUM,
    CoolingSystem,
    DielectricMaterial,
    InterconnectMaterial,
    SemiconductorMaterial,
    get_cooling_spec,
    get_dielectric_spec,
    get_interconnect_spec,
    get_semiconductor_spec,
)

__all__ = [
    "ChipSpec",
    "FabricabilityResult",
    "check_chip_fabricability",
    "extract_chip_spec_from_plan",
    "extract_chip_spec_from_text",
    "MachineSpec",
    "get_machine_spec",
    "BEST_CHIP_MAKING_DEVICE",
    "SOTA_MACHINES",
    "ASML_EXE_5200",
    "ASML_EXE_5000",
    "ASML_NXE_3800E",
    "ASML_NXT_2100I",
    "CANON_NIL_1200",
    "NANGATE45_MACHINE",
    "SemiconductorMaterial",
    "get_semiconductor_spec",
    "SILICON",
    "SILICON_GERMANIUM",
    "MOS2_2D",
    "CARBON_NANOTUBES",
    "GALLIUM_NITRIDE",
    "GALLIUM_ARSENIDE",
    "DielectricMaterial",
    "get_dielectric_spec",
    "HIGH_K_HFO2",
    "SILICON_DIOXIDE",
    "InterconnectMaterial",
    "get_interconnect_spec",
    "COPPER",
    "RUTHENIUM_MOLY",
    "ALUMINUM",
    "CoolingSystem",
    "get_cooling_spec",
    "AIR_COOLED",
    "LIQUID_COLD_PLATE",
    "MICROCHANNEL_IMMERSION",
    "CRYOGENIC",
]
