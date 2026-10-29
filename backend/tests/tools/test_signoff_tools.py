"""Sign-off and waveform sandbox calls (CONTRACTS 3.9 to 3.11) with no Docker: request shape, parsing, old images.

The last tests run the real scripts in the tool image (seeds, strength, wave, and measure's new evidence fields).
They are skipped when Docker or the image (`tools.image`, override with VERITY_TOOLS__IMAGE) is missing.
"""

import json
import shutil
import subprocess

import pytest

from verity.core.mutants import plant
from verity.core.pack import TaskPack
from verity.core.results import VerifyResult
from verity.roles.common import measure_evidence
from verity.tools.base import SCRIPT_PATHS, RawOutput, SandboxCall, SandboxRunner
from verity.tools.local_docker import LocalDockerRunner
from verity.tools.parsers import parse_measure, parse_seeds, parse_wave

from verity_test_support import make_settings


class ScriptedRunner(SandboxRunner):
    def __init__(self, settings, payload: dict) -> None:
        super().__init__(settings, max_parallel=2)
        self.payload = payload
        self.calls: list[SandboxCall] = []

    async def _execute(self, call: SandboxCall) -> RawOutput:
        self.calls.append(call)
        return RawOutput(exit_code=0, stdout=json.dumps(self.payload) + "\n", stderr="", duration_ms=12)

    async def image_digest(self) -> str:
        return "sha256:scripted"


SEEDS_OK = {
    "schema": "verity.seeds/1",
    "status": "pass",
    "lint": {"ok": True, "errors": 0, "warnings": 2, "by_tag": {"UNUSEDSIGNAL": 1, "WIDTHEXPAND": 1}, "latches": 0, "log": "", "tool": "Verilator"},
    "seeds": [
        {"seed": 2, "ok": True, "total": 16, "passed": 16, "failure_count": 0, "failures": []},
        {"seed": 3, "ok": True, "total": 15, "passed": 15, "failure_count": 0, "failures": []},
    ],
    "duration_ms": 900,
}
WAVE_OK = {
    "schema": "verity.wave/1",
    "status": "ok",
    "wave": {
        "case": "count_up",
        "fail_cycle": 4,
        "start_cycle": 0,
        "signals": [
            {"name": "en", "width": 1, "got": ["1", "1"]},
            {"name": "q", "width": 8, "got": ["0x03", "0x05"], "expected": ["0x03", "0x04"]},
        ],
        "note": "n",
    },
}


def rtl_of(pack: TaskPack):
    return pack.validate_rtl(pack.reference_rtl() or "")


async def test_seeds_call_sends_the_seed_list_and_counts_only_full_runs(settings, mac4: TaskPack) -> None:
    runner = ScriptedRunner(settings, SEEDS_OK)
    result = await runner.verify_seeds(rtl_of(mac4), mac4, [2, 3])
    (call,) = runner.calls
    assert call.script == "seeds" and SCRIPT_PATHS["seeds"] == "/verity/bin/seeds"
    assert call.env["VERITY_SEEDS"] == "2,3" and "VERITY_SIM_TIMEOUT_S" in call.env
    assert list(call.work_files) == ["dut.v"]
    assert [r.ok for r in result.seeds] == [True, False], "15 of 15 is not a pass when the pack has 16 tests"
    assert result.lint is not None and result.lint.by_tag == {"UNUSEDSIGNAL": 1, "WIDTHEXPAND": 1}
    assert call.timeout_s > settings.timeouts.sim_s, "the outer timeout covers several seeds"


async def test_strength_call_sends_the_mutants_as_one_json_file(settings, packs) -> None:
    cnt8 = packs["cnt8"]
    mutants = plant(rtl_of(cnt8).text, cnt8)
    runner = ScriptedRunner(settings, {"schema": "verity.strength/1", "status": "ok", "mutants": [{"id": "m01", "status": "caught"}]})
    result = await runner.test_strength(rtl_of(cnt8), cnt8, mutants)
    (call,) = runner.calls
    assert call.script == "strength" and set(call.work_files) == {"dut.v", "mutants.json"}
    body = json.loads(call.work_files["mutants.json"])
    assert [m["id"] for m in body["mutants"]] == [m.id for m in mutants] and body["mutants"][0]["text"] == mutants[0].text
    assert call.env["VERITY_STRENGTH_BUDGET_S"] == str(settings.signoff.strength_budget_s)
    assert result.status == "ok" and result.mutants[0].status == "caught"


async def test_wave_call_names_the_case(settings, packs) -> None:
    runner = ScriptedRunner(settings, WAVE_OK)
    result = await runner.waveform(rtl_of(packs["cnt8"]), packs["cnt8"], "count_up")
    assert runner.calls[0].env["VERITY_WAVE_CASE"] == "count_up" and runner.calls[0].script == "wave"
    assert result.wave is not None and result.wave.fail_cycle == 4
    first = await ScriptedRunner(settings, WAVE_OK).waveform(rtl_of(packs["cnt8"]), packs["cnt8"])
    assert first.status == "ok"


def test_a_misaligned_wave_is_dropped() -> None:
    bad = json.loads(json.dumps(WAVE_OK))
    bad["wave"]["signals"][1]["expected"] = ["0x03"]
    assert parse_wave(bad).wave is None and parse_wave(bad).status == "sim_error"
    assert parse_wave(WAVE_OK).wave is not None


def test_old_and_new_verify_outputs_both_parse() -> None:
    old = {"schema": "verity.verify/1", "status": "pass", "lint": {"ok": True, "errors": 0, "warnings": 1, "log": "x"}}
    new = {**old, "lint": {**old["lint"], "by_tag": {"WIDTHEXPAND": 1}, "latches": 0}}
    assert VerifyResult.model_validate(old).lint.by_tag == {} and VerifyResult.model_validate(old).lint.latches is None
    assert VerifyResult.model_validate(new).lint.by_tag == {"WIDTHEXPAND": 1}


def test_measure_evidence_fields_are_optional_and_lenient() -> None:
    base = {
        "schema": "verity.measure/1",
        "status": "ok",
        "synth": {"ok": True, "area_um2": 100.0, "cell_count": 10, "cells": {"DFF_X1": 2, "NAND2_X1": 8}, "log": ""},
        "sta": {"ok": True, "clock_period_ns": 1.0, "wns_ns": 9.0, "target_period_ns": 10.0, "log": ""},
        "pdk": "Nangate45",
    }
    old = parse_measure(json.loads(json.dumps(base)))
    assert old.sta is not None and old.sta.path is None and old.synth is not None and old.synth.cell_area == {}
    new = json.loads(json.dumps(base))
    new["synth"]["cell_area"] = {"DFF_X1": 9.0, "NAND2_X1": 6.4}
    new["sta"]["path"] = {"startpoint": "q[0]", "endpoint": "q[7]", "start_kind": "register", "end_kind": "register", "depth": 4, "delay_ns": 0.3}
    parsed = parse_measure(new)
    assert parsed.sta is not None and parsed.sta.path is not None and parsed.sta.path.depth == 4
    text = measure_evidence(parsed)
    assert "DFF_X1 (2 cells, 9.0 um2, 9%)" in text and "Flip-flops: 2 (9.0 um2" in text
    assert "from register q[0] to register q[7], 4 logic stages, 0.300 ns" in text
    broken = json.loads(json.dumps(new))
    broken["sta"]["path"] = {"depth": "deep"}
    kept = parse_measure(broken)
    assert kept.status == "ok" and kept.sta is not None and kept.sta.path is None, "a bad path never costs the timing"


# ---------- in the real image ----------
def _image_ready(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture()
def docker_runner(tmp_path):
    settings = make_settings(tmp_path / "data")
    if not _image_ready(settings.tools.image):
        pytest.skip("docker or the verity-tools image is not available")
    return LocalDockerRunner(settings)


def _buggy_cnt8(pack: TaskPack):
    text = (pack.reference_rtl() or "").replace("q <= q + 8'd1;", "q <= (q == 8'd3) ? q + 8'd2 : q + 8'd1;")
    return pack.validate_rtl(text)


async def test_image_seeds_and_lint_tags(docker_runner, packs) -> None:
    cnt8 = packs["cnt8"]
    result = await docker_runner.verify_seeds(rtl_of(cnt8), cnt8, [2, 3, 4])
    if result.status == "internal_error" and "seeds" in result.message:
        pytest.skip("the image predates the sign-off scripts")
    assert result.status == "pass", result.message
    assert [r.seed for r in result.seeds] == [2, 3, 4] and all(r.ok for r in result.seeds)
    assert result.lint is not None and result.lint.latches == 0


async def test_image_planted_bugs_are_caught(docker_runner, packs) -> None:
    cnt8 = packs["cnt8"]
    mutants = plant(rtl_of(cnt8).text, cnt8)
    result = await docker_runner.test_strength(rtl_of(cnt8), cnt8, mutants)
    assert result.status == "ok", result.message
    assert {r.id for r in result.mutants} == {m.id for m in mutants}
    assert sum(1 for r in result.mutants if r.status == "caught") >= len(mutants) - 1


async def test_image_waveform_of_a_failing_counter(docker_runner, packs) -> None:
    cnt8 = packs["cnt8"]
    result = await docker_runner.waveform(_buggy_cnt8(cnt8), cnt8, "count_up")
    assert result.status == "ok", result.message
    wave = result.wave
    assert wave is not None and wave.case == "count_up" and wave.fail_cycle == 4
    q = next(s for s in wave.signals if s.name == "q")
    col = wave.fail_cycle - wave.start_cycle
    assert q.got[col] == "0x05" and q.expected is not None and q.expected[col] == "0x04"
    passing = await docker_runner.waveform(rtl_of(cnt8), cnt8, None)
    assert passing.status == "ok" and passing.wave is not None and passing.wave.fail_cycle is None


async def test_image_measure_reports_cell_areas_and_the_critical_path(docker_runner, mac4: TaskPack) -> None:
    result = await docker_runner.measure(rtl_of(mac4), mac4)
    assert result.status == "ok", result.message
    assert result.synth is not None and result.synth.cell_area and sum(result.synth.cell_area.values()) == pytest.approx(result.synth.area_um2, rel=0.01)
    path = result.sta.path if result.sta else None
    assert path is not None and path.depth > 0 and path.start_kind == "register" and not path.startpoint.startswith("_")
