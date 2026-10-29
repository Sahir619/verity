"""Power estimate: parsing a real OpenSTA `report_power` output, the PPA mapping, and one real sandbox measurement.

The sample below was captured from verity-tools:0.1 (OpenSTA 3.1.0) on the mac4 reference netlist with
`set_power_activity -input -activity 0.1 -duty 0.5` and `report_power -digits 6`.
The integration test needs Docker and the image; it is skipped when either is missing.
"""

import shutil
import subprocess
from importlib.machinery import SourceFileLoader

import pytest

from verity.config import AGENT_ROOT
from verity.core.pack import TaskPack
from verity.core.results import MeasureResult
from verity.tools.local_docker import LocalDockerRunner
from verity.tools.parsers import parse_measure

from verity_test_support import make_settings

measure = SourceFileLoader("verity_measure_script", str(AGENT_ROOT / "sandbox" / "bin" / "measure")).load_module()

REPORT_POWER = """\
Group                    Internal    Switching      Leakage        Total
                            Power        Power        Power        Power (Watts)
------------------------------------------------------------------------
Sequential           3.654014e-05 2.253764e-06 2.161823e-06 4.095572e-05   2.2%
Combinational        1.000887e-03 7.820399e-04 4.651128e-05 1.829438e-03  97.8%
Clock                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Macro                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Pad                  0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
------------------------------------------------------------------------
Total                1.037427e-03 7.842936e-04 4.867310e-05 1.870394e-03 100.0%
                            55.5%        41.9%         2.6%
"""

MEASURE_BASE = {
    "schema": "verity.measure/1",
    "status": "ok",
    "synth": {"ok": True, "area_um2": 1840.2, "cell_count": 412, "cells": {}, "log": "", "tool": "Yosys"},
    "sta": {"ok": True, "clock_period_ns": 3.2, "wns_ns": 6.8, "target_period_ns": 10.0, "clocked": True, "log": "", "tool": "OpenSTA"},
    "circuit": None,
    "pdk": "Nangate45",
}


def test_report_power_total_row_is_converted_to_mw() -> None:
    parsed = measure.parse_report_power(REPORT_POWER)
    assert parsed is not None
    assert parsed["power_mw"] == pytest.approx(1.870394)
    assert parsed["internal_mw"] == pytest.approx(1.037427)
    assert parsed["switching_mw"] == pytest.approx(0.7842936)
    assert parsed["leakage_mw"] == pytest.approx(0.0486731)


@pytest.mark.parametrize("bad", ["", "Error: no liberty\n", REPORT_POWER.replace("Power (Watts)", "Power"), REPORT_POWER.replace("Total  ", "Sum    ")])
def test_unparseable_power_is_none(bad: str) -> None:
    assert measure.parse_report_power(bad) is None


def test_power_script_uses_the_fixed_activity_and_the_pack_clock() -> None:
    script = measure.power_script("mac4", measure.Path("/work/out/netlist.v"), "clk", 10.0)
    assert "create_clock -name clk -period 10.0" in script
    assert "set_power_activity -input -activity 0.1 -duty 0.5" in script
    assert "report_power" in script


def test_power_maps_to_ppa_and_old_results_stay_null() -> None:
    power = {"ok": True, "power_mw": 1.870394, "method": "vectorless", "input_activity": 0.1, "reference_clock_ns": 10.0}
    assert parse_measure({**MEASURE_BASE, "power": power}).to_ppa().power_mw == 1.8704
    assert parse_measure(dict(MEASURE_BASE)).to_ppa().power_mw is None  # result from an older image
    assert parse_measure({**MEASURE_BASE, "power": {**power, "ok": False, "power_mw": None}}).to_ppa().power_mw is None
    bad = parse_measure({**MEASURE_BASE, "power": {**power, "power_mw": -1.0}})
    assert bad.status == "ok" and bad.to_ppa().power_mw is None


def _docker_image_ready(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


async def test_measure_in_the_sandbox_reports_positive_power(tmp_path, mac4: TaskPack) -> None:
    settings = make_settings(tmp_path / "data")
    if not _docker_image_ready(settings.tools.image):
        pytest.skip("docker or the verity-tools image is not available")
    rtl = mac4.validate_rtl(mac4.reference_rtl() or "", max_bytes=settings.rtl.max_bytes)
    result: MeasureResult = await LocalDockerRunner(settings).measure(rtl, mac4)
    assert result.status == "ok", result.message
    assert result.power is not None and result.power.ok and result.power.method == "vectorless"
    assert result.power.reference_clock_ns == mac4.target_clock_ns
    ppa = result.to_ppa()
    assert ppa.power_mw is not None and ppa.power_mw > 0
    assert result.power.leakage_mw is not None and result.power.leakage_mw < ppa.power_mw
