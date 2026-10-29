"""Materials science physical properties and fundamental limits for semiconductors.

Captures physical constants and hard physical boundaries:
- Silicon (Bulk, FinFET, GAA-nanosheet, SOI):
  Lattice constant: 0.5431 nm
  Direct source-drain quantum tunneling limit: 5.0 nm physical gate length
  (Clarification: Commercial 'Intel 18A' or '1.8nm' nodes feature physical gate lengths of ~12 nm
   and contacted poly pitches of ~45 nm. A physical dimension of 1.8 nm in silicon is physically
   impossible due to quantum tunneling and atomic lattice breakdown).
- SiGe / Ge: Quantum tunneling limit: 6.0 nm
- 2D TMD (MoS2): Monolayer 0.65 nm, heavier effective mass suppresses tunneling down to ~1.0 nm (lab only)
- Carbon Nanotubes (CNT): ~1.0 nm tunneling limit (lab only, VLSI yield not commercial)
- GaN / SiC: Wide bandgap, high voltage/power, minimum feature ~50 nm
- Gate Dielectrics: SiO2 (min 1.2 nm), HfO2 High-k (min 1.6 nm, EOT 0.6 nm)
- Interconnects: Cu (min width 8.0 nm, electromigration 2.0 MA/cm2), Ru/Mo/Co (min width 6.0 nm)
- Thermal power density dissipation limits per cooling method
"""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SemiconductorMaterial:
    """Fundamental physical limits of a semiconductor channel material."""

    id: str
    name: str
    lattice_constant_nm: float  # crystal unit cell dimension
    effective_electron_mass_ratio: float  # m* / m0
    direct_tunneling_limit_nm: float  # physical gate length below which quantum tunneling ruins off-state
    critical_breakdown_field_v_nm: float  # electric field breakdown threshold
    bandgap_ev: float  # bandgap energy
    thermal_conductivity_w_m_k: float  # thermal conductivity
    max_junction_temp_c: float  # maximum operational junction temperature
    max_frequency_ghz: float  # maximum room-temperature digital clocking cutoff
    min_commercial_feature_nm: float  # smallest reliably mass-produced physical feature
    commercial_readiness: str  # "high_volume_manufacturing", "specialty_commercial", "laboratory_prototype"
    notes: str


@dataclass(frozen=True, slots=True)
class DielectricMaterial:
    """Physical limits of gate insulator / dielectric."""

    id: str
    name: str
    dielectric_constant_k: float  # relative permittivity k
    min_physical_thickness_nm: float  # minimum thickness before catastrophic gate leakage
    breakdown_field_v_nm: float  # dielectric breakdown electric field
    notes: str


@dataclass(frozen=True, slots=True)
class InterconnectMaterial:
    """Physical limits of metal wiring and interconnects."""

    id: str
    name: str
    min_line_width_nm: float  # minimum wire width before scattering causes resistance surge
    max_current_density_ma_cm2: float  # electromigration threshold (MA/cm2 = 1e6 A/cm2)
    bulk_resistivity_uohm_cm: float  # bulk resistivity
    notes: str


@dataclass(frozen=True, slots=True)
class CoolingSystem:
    """Thermal dissipation limits for chip packaging."""

    id: str
    name: str
    max_power_density_w_cm2: float  # maximum safe steady-state thermal power density
    description: str


# ======================================================================
# Semiconductor Database
# ======================================================================

SILICON = SemiconductorMaterial(
    id="silicon",
    name="Silicon (Si CMOS / GAA Nanosheet / FinFET)",
    lattice_constant_nm=0.5431,
    effective_electron_mass_ratio=0.19,
    direct_tunneling_limit_nm=5.0,  # Below 5nm physical gate length, quantum tunneling makes Ioff > 100 nA/um
    critical_breakdown_field_v_nm=0.03,  # ~300 kV/cm
    bandgap_ev=1.12,
    thermal_conductivity_w_m_k=149.0,
    max_junction_temp_c=125.0,
    max_frequency_ghz=5.5,  # Practical upper limit for standard room-temp CMOS
    min_commercial_feature_nm=8.0,  # ASML High-NA single-exposure resolution
    commercial_readiness="high_volume_manufacturing",
    notes=(
        "Standard silicon CMOS. Commercial 'Intel 18A' (1.8nm) or 'TSMC A16' (1.6nm) are marketing node names; "
        "their actual physical gate length is ~12-14 nm with ~45 nm contacted poly pitch. "
        "A physical feature of 1.8 nm in silicon is physically impossible due to quantum tunneling and dopant randomness."
    ),
)

SILICON_GERMANIUM = SemiconductorMaterial(
    id="sige",
    name="Silicon-Germanium (SiGe) / Germanium (Ge)",
    lattice_constant_nm=0.565,
    effective_electron_mass_ratio=0.12,
    direct_tunneling_limit_nm=6.0,
    critical_breakdown_field_v_nm=0.015,
    bandgap_ev=0.66,
    thermal_conductivity_w_m_k=60.0,
    max_junction_temp_c=105.0,
    max_frequency_ghz=6.5,
    min_commercial_feature_nm=10.0,
    commercial_readiness="high_volume_manufacturing",
    notes="High hole-mobility material, but higher tunneling leakage and narrower bandgap than pure Si.",
)

MOS2_2D = SemiconductorMaterial(
    id="mos2",
    name="Molybdenum Disulfide (2D TMD MoS2)",
    lattice_constant_nm=0.316,
    effective_electron_mass_ratio=0.55,
    direct_tunneling_limit_nm=1.0,  # High effective mass suppresses tunneling down to ~1nm in research prototypes
    critical_breakdown_field_v_nm=0.08,
    bandgap_ev=1.8,
    thermal_conductivity_w_m_k=35.0,
    max_junction_temp_c=150.0,
    max_frequency_ghz=3.0,
    min_commercial_feature_nm=1.0,
    commercial_readiness="laboratory_prototype",
    notes="Experimental 2D material. Demonstrates sub-2nm gate feasibility in university labs, but no 300mm commercial fab line exists.",
)

CARBON_NANOTUBES = SemiconductorMaterial(
    id="cnt",
    name="Carbon Nanotubes (Semiconducting CNT)",
    lattice_constant_nm=0.246,
    effective_electron_mass_ratio=0.06,
    direct_tunneling_limit_nm=1.0,
    critical_breakdown_field_v_nm=0.1,
    bandgap_ev=0.8,
    thermal_conductivity_w_m_k=3000.0,
    max_junction_temp_c=175.0,
    max_frequency_ghz=8.0,
    min_commercial_feature_nm=1.5,
    commercial_readiness="laboratory_prototype",
    notes="High ballistic transport and thermal conductivity, but alignment defects prevent commercial VLSI yield.",
)

GALLIUM_NITRIDE = SemiconductorMaterial(
    id="gan",
    name="Gallium Nitride (GaN)",
    lattice_constant_nm=0.3189,
    effective_electron_mass_ratio=0.20,
    direct_tunneling_limit_nm=20.0,
    critical_breakdown_field_v_nm=0.33,  # 3.3 MV/cm
    bandgap_ev=3.4,
    thermal_conductivity_w_m_k=130.0,
    max_junction_temp_c=200.0,
    max_frequency_ghz=10.0,
    min_commercial_feature_nm=50.0,
    commercial_readiness="specialty_commercial",
    notes="Wide bandgap power/RF semiconductor. Lacks high-density complementary p-channel logic for dense sub-10nm digital VLSI.",
)

GALLIUM_ARSENIDE = SemiconductorMaterial(
    id="gaas",
    name="Gallium Arsenide (GaAs)",
    lattice_constant_nm=0.5653,
    effective_electron_mass_ratio=0.067,
    direct_tunneling_limit_nm=15.0,
    critical_breakdown_field_v_nm=0.04,
    bandgap_ev=1.42,
    thermal_conductivity_w_m_k=55.0,
    max_junction_temp_c=125.0,
    max_frequency_ghz=8.0,
    min_commercial_feature_nm=28.0,
    commercial_readiness="specialty_commercial",
    notes="High electron mobility for RF and optoelectronics, but poor native oxide restricts deep sub-micron scaling.",
)

SEMICONDUCTORS: Mapping[str, SemiconductorMaterial] = {
    "silicon": SILICON,
    "si": SILICON,
    "sige": SILICON_GERMANIUM,
    "ge": SILICON_GERMANIUM,
    "mos2": MOS2_2D,
    "2d_tmd": MOS2_2D,
    "cnt": CARBON_NANOTUBES,
    "carbon_nanotubes": CARBON_NANOTUBES,
    "gan": GALLIUM_NITRIDE,
    "gaas": GALLIUM_ARSENIDE,
}


# ======================================================================
# Gate Dielectric Database
# ======================================================================

HIGH_K_HFO2 = DielectricMaterial(
    id="high_k",
    name="Hafnium Dioxide (HfO2) High-k Metal Gate",
    dielectric_constant_k=25.0,
    min_physical_thickness_nm=1.6,  # EOT ~ 0.6nm; below 3 atomic layers direct punchthrough occurs
    breakdown_field_v_nm=0.8,  # 8 MV/cm
    notes="Modern High-k gate dielectric used in all advanced nodes from 45nm down to 2nm.",
)

SILICON_DIOXIDE = DielectricMaterial(
    id="sio2",
    name="Silicon Dioxide (SiO2)",
    dielectric_constant_k=3.9,
    min_physical_thickness_nm=1.2,  # Direct quantum tunneling through SiO2 exceeds 1 A/cm2 below 1.2nm
    breakdown_field_v_nm=1.0,  # 10 MV/cm
    notes="Legacy gate dielectric for nodes >= 65nm; now used primarily for shallow trench isolation and interconnects.",
)

DIELECTRICS: Mapping[str, DielectricMaterial] = {
    "high_k": HIGH_K_HFO2,
    "hfo2": HIGH_K_HFO2,
    "sio2": SILICON_DIOXIDE,
    "oxide": HIGH_K_HFO2,
}


# ======================================================================
# Interconnect Metal Database
# ======================================================================

COPPER = InterconnectMaterial(
    id="copper",
    name="Copper (Cu) with Ta/TaN/Ru Barrier",
    min_line_width_nm=8.0,
    max_current_density_ma_cm2=2.0,  # 2 MA/cm2 electromigration limit
    bulk_resistivity_uohm_cm=1.68,
    notes="Standard dual-damascene interconnect metal. At wire widths < 10nm, surface scattering increases resistivity.",
)

RUTHENIUM_MOLY = InterconnectMaterial(
    id="ruthenium",
    name="Ruthenium (Ru) / Molybdenum (Mo) / Cobalt (Co)",
    min_line_width_nm=6.0,
    max_current_density_ma_cm2=5.0,  # Much higher resistance to electromigration than copper
    bulk_resistivity_uohm_cm=7.1,
    notes="Advanced metallization for sub-2nm nodes, replacing copper in lower tight-pitch metal layers.",
)

ALUMINUM = InterconnectMaterial(
    id="aluminum",
    name="Aluminum (Al)",
    min_line_width_nm=50.0,
    max_current_density_ma_cm2=0.5,
    bulk_resistivity_uohm_cm=2.65,
    notes="Legacy interconnect metal, used for pad layers and older mature process nodes (> 130nm).",
)

INTERCONNECTS: Mapping[str, InterconnectMaterial] = {
    "copper": COPPER,
    "cu": COPPER,
    "ruthenium": RUTHENIUM_MOLY,
    "ru": RUTHENIUM_MOLY,
    "molybdenum": RUTHENIUM_MOLY,
    "mo": RUTHENIUM_MOLY,
    "cobalt": RUTHENIUM_MOLY,
    "aluminum": ALUMINUM,
    "al": ALUMINUM,
}


# ======================================================================
# Cooling Solutions Database
# ======================================================================

AIR_COOLED = CoolingSystem(
    id="air",
    name="Forced Air Cooling with Heat Sink",
    max_power_density_w_cm2=100.0,  # Standard consumer/commercial air heatsink
    description="Standard copper/aluminum fin heatsink with fan. Dissipates up to 100 W/cm2.",
)

LIQUID_COLD_PLATE = CoolingSystem(
    id="liquid",
    name="Liquid Cold Plate / Closed Loop Liquid Cooling",
    max_power_density_w_cm2=200.0,
    description="Direct-to-die or cold plate water/glycol cooling. Dissipates up to 200 W/cm2.",
)

MICROCHANNEL_IMMERSION = CoolingSystem(
    id="microchannel",
    name="Microchannel Direct Liquid / Phase-Change Immersion",
    max_power_density_w_cm2=350.0,
    description="Direct silicon microchannel cooling or two-phase liquid immersion. Dissipates up to 350 W/cm2.",
)

CRYOGENIC = CoolingSystem(
    id="cryo",
    name="Cryogenic Liquid Nitrogen (77K)",
    max_power_density_w_cm2=500.0,
    description="Liquid nitrogen cooling for specialized high-performance computing.",
)

COOLING_SYSTEMS: Mapping[str, CoolingSystem] = {
    "air": AIR_COOLED,
    "passive": AIR_COOLED,
    "liquid": LIQUID_COLD_PLATE,
    "cold_plate": LIQUID_COLD_PLATE,
    "microchannel": MICROCHANNEL_IMMERSION,
    "immersion": MICROCHANNEL_IMMERSION,
    "cryo": CRYOGENIC,
    "cryogenic": CRYOGENIC,
}


def get_semiconductor_spec(name: str | None = None) -> SemiconductorMaterial:
    """Retrieve semiconductor material properties, defaulting to standard Silicon."""
    if not name:
        return SILICON
    key = name.strip().lower().replace("-", "_")
    return SEMICONDUCTORS.get(key, SILICON)


def get_dielectric_spec(name: str | None = None) -> DielectricMaterial:
    """Retrieve dielectric material properties, defaulting to High-k HfO2."""
    if not name:
        return HIGH_K_HFO2
    key = name.strip().lower().replace("-", "_")
    return DIELECTRICS.get(key, HIGH_K_HFO2)


def get_interconnect_spec(name: str | None = None) -> InterconnectMaterial:
    """Retrieve interconnect material properties, defaulting to Copper."""
    if not name:
        return COPPER
    key = name.strip().lower().replace("-", "_")
    return INTERCONNECTS.get(key, COPPER)


def get_cooling_spec(name: str | None = None) -> CoolingSystem:
    """Retrieve cooling solution limits, defaulting to Air Cooling."""
    if not name:
        return AIR_COOLED
    key = name.strip().lower().replace("-", "_")
    return COOLING_SYSTEMS.get(key, AIR_COOLED)
