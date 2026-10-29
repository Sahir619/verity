"""Extraction helpers to build ChipSpec from plans, task packs, PPA, and text."""

import re
from typing import Any

from ..contracts import PPA
from ..core.pack import TaskPack
from ..core.llm_types import PlanOut
from .checker import ChipSpec

# Regex patterns for extracting hardware parameters from text
_NM_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:nm|nanometer|nanometers|a|angstrom)\b",
    re.IGNORECASE,
)
_VOLT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:v|volt|volts)\b", re.IGNORECASE)
_GHZ_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:ghz|gigahertz)\b", re.IGNORECASE)
_MHZ_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:mhz|megahertz)\b", re.IGNORECASE)
_WATT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:w|watt|watts|mw|milliwatt|milliwatts)\b", re.IGNORECASE)


def extract_chip_spec_from_text(text: str) -> ChipSpec:
    """Extract physical parameters from a free-text specification or description."""
    spec = ChipSpec()
    text_lower = text.lower()

    # Material keywords
    if "mos2" in text_lower or "2d tmd" in text_lower:
        spec.channel_material = "mos2"
    elif "cnt" in text_lower or "carbon nanotube" in text_lower:
        spec.channel_material = "cnt"
    elif "gan" in text_lower or "gallium nitride" in text_lower:
        spec.channel_material = "gan"
    elif "gaas" in text_lower or "gallium arsenide" in text_lower:
        spec.channel_material = "gaas"
    elif "sige" in text_lower or "germanium" in text_lower:
        spec.channel_material = "sige"
    else:
        spec.channel_material = "silicon"

    # Cooling
    if "cryo" in text_lower or "liquid nitrogen" in text_lower:
        spec.cooling_method = "cryo"
    elif "microchannel" in text_lower or "immersion" in text_lower:
        spec.cooling_method = "microchannel"
    elif "liquid" in text_lower or "water cooling" in text_lower:
        spec.cooling_method = "liquid"
    else:
        spec.cooling_method = "air"

    # Interconnect
    if "ruthenium" in text_lower or "ru interconnect" in text_lower:
        spec.interconnect_metal = "ruthenium"
    elif "aluminum" in text_lower:
        spec.interconnect_metal = "aluminum"
    else:
        spec.interconnect_metal = "copper"

    # Extract nanometer / process node
    nm_match = _NM_PATTERN.search(text)
    if nm_match:
        val = float(nm_match.group(1))
        # Disambiguate "18A" / 18 angstrom
        if "a" in nm_match.group(0).lower() and val > 10:
            val = val / 10.0
        spec.feature_size_nm = val
        spec.process_node_name = nm_match.group(0)

    # Voltage
    volt_match = _VOLT_PATTERN.search(text)
    if volt_match:
        spec.supply_voltage_v = float(volt_match.group(1))

    # Frequency
    ghz_match = _GHZ_PATTERN.search(text)
    if ghz_match:
        spec.clock_frequency_ghz = float(ghz_match.group(1))
    else:
        mhz_match = _MHZ_PATTERN.search(text)
        if mhz_match:
            spec.clock_frequency_ghz = float(mhz_match.group(1)) / 1000.0

    # Power
    watt_match = _WATT_PATTERN.search(text)
    if watt_match:
        val = float(watt_match.group(1))
        if "mw" in watt_match.group(0).lower():
            val = val / 1000.0
        spec.power_w = val

    return spec


def extract_chip_spec_from_plan(
    plan: PlanOut,
    pack: TaskPack,
    ppa: PPA | None = None,
) -> ChipSpec:
    """Extract ChipSpec combining the plan, task pack contract, and optional synthesis PPA."""
    # Concatenate plan text fields (excluding risks, where a plan may say "no 1.8nm needed")
    plan_text_parts = [
        plan.summary,
        plan.interfaces,
        plan.data_widths,
        plan.verification,
    ]
    for block in plan.blocks:
        plan_text_parts.append(block.name)
        plan_text_parts.append(block.reason)
        for pin in block.pins:
            plan_text_parts.append(pin)
        for variant in block.variants:
            plan_text_parts.append(f"{variant.label} {variant.value}")

    full_text = " ".join(plan_text_parts)
    spec = extract_chip_spec_from_text(full_text)

    # Derive frequency from pack target clock if not explicitly stated in plan
    if spec.clock_frequency_ghz is None and pack.target_clock_ns > 0:
        spec.clock_frequency_ghz = round(1.0 / pack.target_clock_ns, 4)

    # If PDK is known and no feature size was stated in plan text, default to PDK feature size
    if spec.feature_size_nm is None:
        pdk_name = (ppa.pdk if ppa and ppa.pdk else "Nangate45").lower()
        if "45" in pdk_name:
            spec.feature_size_nm = 45.0
            spec.process_node_name = "Nangate45"
        elif "130" in pdk_name:
            spec.feature_size_nm = 130.0
            spec.process_node_name = "sky130"

    # Merge PPA metrics if available
    if ppa is not None:
        if ppa.area_um2 is not None:
            spec.area_um2 = ppa.area_um2
            spec.die_area_mm2 = ppa.area_um2 * 1e-6
        if ppa.cell_count is not None:
            spec.cell_count = ppa.cell_count
        if ppa.clock_period_ns is not None and ppa.clock_period_ns > 0:
            spec.clock_frequency_ghz = round(1.0 / ppa.clock_period_ns, 4)
        if ppa.power_mw is not None:
            spec.power_w = ppa.power_mw / 1000.0

    return spec
