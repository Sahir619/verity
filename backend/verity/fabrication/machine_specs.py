"""State-of-the-art lithography and chip manufacturing machine specifications.

Contains verified specifications of the world's best chip-making devices:
- ASML TWINSCAN EXE:5200 / EXE:5000 (High-NA EUV: 0.55 NA, 8 nm single-exposure resolution)
- ASML TWINSCAN NXE:3800E / NXE:3600D (Low-NA EUV: 0.33 NA, 13 nm single-exposure resolution)
- ASML TWINSCAN NXT:2100i (ArFi Immersion DUV: 1.35 NA, 38 nm single-exposure resolution)
- Canon FPA-1200NZ2C (Nanoimprint Lithography NIL: 10 nm minimum feature)
- Standard Open PDK foundry machines (Nangate45, SkyWater 130)

Used by the deterministic fabricability checker to verify whether a proposed chip
can physically be patterned and manufactured.
"""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MachineSpec:
    """Hardware capabilities of a semiconductor manufacturing / lithography machine."""

    id: str
    name: str
    manufacturer: str
    technology: str  # e.g. "High-NA EUV", "Low-NA EUV", "Immersion DUV", "Nanoimprint"
    wavelength_nm: float  # exposure light wavelength (e.g. 13.5 nm EUV, 193 nm DUV)
    numerical_aperture: float  # lens numerical aperture NA
    rayleigh_k1: float  # process difficulty factor k1 in CD = k1 * (lambda / NA)
    single_exposure_resolution_nm: float  # absolute single exposure CD limit
    multi_patterning_min_feature_nm: float  # multi-patterning (SADP/SAQP) physical limit
    single_exposure_pitch_nm: float  # dense line/space pitch limit
    contacted_poly_pitch_nm: float  # minimum contacted poly / gate pitch (CPP)
    min_metal_pitch_nm: float  # minimum metal interconnect pitch (MMP)
    overlay_accuracy_nm: float  # wafer stage alignment / overlay accuracy
    max_reticle_field_w_mm: float  # exposure slit width
    max_reticle_field_h_mm: float  # exposure scan height
    max_die_area_mm2: float  # maximum single-exposure die area
    wafer_diameter_mm: float  # silicon wafer diameter
    description: str

    @property
    def optical_resolution_limit_nm(self) -> float:
        """Rayleigh optical resolution CD = k1 * (lambda / NA)."""
        return round(self.rayleigh_k1 * (self.wavelength_nm / self.numerical_aperture), 2)


# ======================================================================
# Known Machines Database
# ======================================================================

# ASML TWINSCAN EXE:5200 - The world's most advanced lithography tool in commercial production
ASML_EXE_5200 = MachineSpec(
    id="asml_exe_5200",
    name="ASML TWINSCAN EXE:5200",
    manufacturer="ASML",
    technology="High-NA Extreme Ultraviolet (High-NA EUV)",
    wavelength_nm=13.5,
    numerical_aperture=0.55,
    rayleigh_k1=0.32,
    single_exposure_resolution_nm=8.0,
    multi_patterning_min_feature_nm=5.0,
    single_exposure_pitch_nm=16.0,
    contacted_poly_pitch_nm=40.0,
    min_metal_pitch_nm=16.0,
    overlay_accuracy_nm=1.1,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=16.5,  # anamorphic 8x/4x half-field
    max_die_area_mm2=429.0,
    wafer_diameter_mm=300.0,
    description="ASML 0.55 NA High-NA EUV high-volume manufacturing tool with anamorphic optics. SOTA 2nm/sub-2nm node.",
)

# ASML TWINSCAN EXE:5000 - Development pathfinder High-NA tool
ASML_EXE_5000 = MachineSpec(
    id="asml_exe_5000",
    name="ASML TWINSCAN EXE:5000",
    manufacturer="ASML",
    technology="High-NA Extreme Ultraviolet (High-NA EUV)",
    wavelength_nm=13.5,
    numerical_aperture=0.55,
    rayleigh_k1=0.33,
    single_exposure_resolution_nm=8.0,
    multi_patterning_min_feature_nm=5.0,
    single_exposure_pitch_nm=16.0,
    contacted_poly_pitch_nm=42.0,
    min_metal_pitch_nm=16.0,
    overlay_accuracy_nm=1.2,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=16.5,
    max_die_area_mm2=429.0,
    wafer_diameter_mm=300.0,
    description="ASML 0.55 NA High-NA EUV R&D and qualification system for 2nm logic and high-density DRAM.",
)

# ASML TWINSCAN NXE:3800E - Leading-edge standard EUV tool
ASML_NXE_3800E = MachineSpec(
    id="asml_nxe_3800e",
    name="ASML TWINSCAN NXE:3800E",
    manufacturer="ASML",
    technology="Standard Extreme Ultraviolet (Low-NA EUV)",
    wavelength_nm=13.5,
    numerical_aperture=0.33,
    rayleigh_k1=0.32,
    single_exposure_resolution_nm=13.0,
    multi_patterning_min_feature_nm=7.0,
    single_exposure_pitch_nm=26.0,
    contacted_poly_pitch_nm=50.0,
    min_metal_pitch_nm=21.0,
    overlay_accuracy_nm=1.5,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=33.0,  # full field
    max_die_area_mm2=858.0,
    wafer_diameter_mm=300.0,
    description="ASML 0.33 NA EUV tool powering high-volume 3nm and 4nm nodes.",
)

# ASML TWINSCAN NXT:2100i - Leading deep ultraviolet immersion scanner
ASML_NXT_2100I = MachineSpec(
    id="asml_nxt_2100i",
    name="ASML TWINSCAN NXT:2100i",
    manufacturer="ASML",
    technology="Argon Fluoride Immersion DUV (ArFi)",
    wavelength_nm=193.0,
    numerical_aperture=1.35,  # water immersion n=1.44
    rayleigh_k1=0.28,
    single_exposure_resolution_nm=38.0,
    multi_patterning_min_feature_nm=10.0,
    single_exposure_pitch_nm=76.0,
    contacted_poly_pitch_nm=84.0,
    min_metal_pitch_nm=40.0,
    overlay_accuracy_nm=2.0,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=33.0,
    max_die_area_mm2=858.0,
    wafer_diameter_mm=300.0,
    description="ArF immersion DUV scanner for critical DUV layers and multi-patterning nodes (7nm to 28nm).",
)

# Canon FPA-1200NZ2C - Nanoimprint lithography
CANON_NIL_1200 = MachineSpec(
    id="canon_fpa_1200nz2c",
    name="Canon FPA-1200NZ2C",
    manufacturer="Canon",
    technology="Nanoimprint Lithography (NIL)",
    wavelength_nm=0.0,  # mechanical imprint mask
    numerical_aperture=1.0,
    rayleigh_k1=0.0,
    single_exposure_resolution_nm=10.0,
    multi_patterning_min_feature_nm=10.0,
    single_exposure_pitch_nm=28.0,
    contacted_poly_pitch_nm=60.0,
    min_metal_pitch_nm=28.0,
    overlay_accuracy_nm=2.4,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=33.0,
    max_die_area_mm2=858.0,
    wafer_diameter_mm=300.0,
    description="Canon nanoimprint lithography tool for sub-15nm patterns without EUV optics.",
)

# Nangate45 reference machine (matching Verity's default open PDK)
NANGATE45_MACHINE = MachineSpec(
    id="nangate45",
    name="Nangate45 Reference Fab Line",
    manufacturer="Generic Foundry",
    technology="Dry Argon Fluoride DUV (ArF)",
    wavelength_nm=193.0,
    numerical_aperture=0.85,
    rayleigh_k1=0.35,
    single_exposure_resolution_nm=45.0,
    multi_patterning_min_feature_nm=40.0,
    single_exposure_pitch_nm=140.0,
    contacted_poly_pitch_nm=190.0,
    min_metal_pitch_nm=140.0,
    overlay_accuracy_nm=5.0,
    max_reticle_field_w_mm=26.0,
    max_reticle_field_h_mm=33.0,
    max_die_area_mm2=858.0,
    wafer_diameter_mm=300.0,
    description="45nm open-cell reference foundry line matching Nangate Open Cell Library typical.",
)

# Registry of known machines
SOTA_MACHINES: Mapping[str, MachineSpec] = {
    "asml_exe_5200": ASML_EXE_5200,
    "asml_exe_5000": ASML_EXE_5000,
    "asml_nxe_3800e": ASML_NXE_3800E,
    "asml_nxt_2100i": ASML_NXT_2100I,
    "canon_fpa_1200nz2c": CANON_NIL_1200,
    "nangate45": NANGATE45_MACHINE,
}

# The absolute best chip-making machine currently available
BEST_CHIP_MAKING_DEVICE: MachineSpec = ASML_EXE_5200


def get_machine_spec(identifier: str | None = None) -> MachineSpec:
    """Get machine specification by ID or alias, defaulting to the world's best machine (ASML EXE:5200)."""
    if not identifier:
        return BEST_CHIP_MAKING_DEVICE
    key = identifier.strip().lower().replace("-", "_").replace(" ", "_")
    if key in SOTA_MACHINES:
        return SOTA_MACHINES[key]
    # Common aliases
    if any(tag in key for tag in ("exe", "high_na", "highna", "5200", "5000", "best", "sota")):
        return ASML_EXE_5200
    if any(tag in key for tag in ("nxe", "low_na", "lowna", "3800", "3600", "euv")):
        return ASML_NXE_3800E
    if any(tag in key for tag in ("nxt", "2100", "duv", "arfi", "immersion")):
        return ASML_NXT_2100I
    if any(tag in key for tag in ("nil", "nanoimprint", "canon")):
        return CANON_NIL_1200
    if any(tag in key for tag in ("nangate", "45nm", "nangate45")):
        return NANGATE45_MACHINE
    return BEST_CHIP_MAKING_DEVICE
