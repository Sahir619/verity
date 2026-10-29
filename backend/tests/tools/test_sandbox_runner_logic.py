"""Sandbox result parsing and the shared runner logic (retries, exit codes, timeouts, cache), with no Docker.

A scripted runner replaces `_execute`, so every branch of CONTRACTS 3.4 is exercised: exit 0 parse, exit 2 input error,
exit 3 and other codes retry then fail, outer timeout becomes a result, unparseable output is an infrastructure error.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from verity.core import captions as cap
from verity.core.errors import ToolInfraError
from verity.core.pack import TaskPack
from verity.core.results import EquivalenceResult, VerifyResult
from verity.tools import base, local_docker
from verity.tools.base import FatalToolError, RawOutput, SandboxCall, SandboxRunner
from verity.tools.local_docker import LocalDockerRunner
from verity.tools.parse import SandboxOutputError, parse_stdout_json
from verity.tools.parsers import parse_eqy, parse_measure, parse_verify
from verity.tools.process import ProcessResult

VERIFY_PASS = {
    "schema": "verity.verify/1",
    "status": "pass",
    "lint": {"ok": True, "errors": 0, "warnings": 1, "log": "%Warning-WIDTH: x", "tool": "Verilator 5.028"},
    "contract": {"ok": True, "errors": [], "ports": []},
    "sim": {"ok": True, "total": 16, "passed": 16, "failure_count": 0, "failures": [], "seed": 1, "log": "", "tool": "iverilog + cocotb"},
    "duration_ms": 10,
    "message": "",
}
MEASURE_OK = {
    "schema": "verity.measure/1",
    "status": "ok",
    "synth": {"ok": True, "area_um2": 1840.2, "cell_count": 412, "cells": {"NAND2_X1": 40}, "log": "", "tool": "Yosys 0.45"},
    "sta": {"ok": True, "clock_period_ns": 3.2, "wns_ns": 6.8, "target_period_ns": 10.0, "clocked": True, "log": "", "tool": "OpenSTA"},
    "circuit": None,
    "pdk": "Nangate45",
    "duration_ms": 18000,
    "message": "",
}


# ---------- stdout parsing ----------
def test_last_json_line_wins_and_chatter_is_ignored() -> None:
    out = 'yosys chatter\n{"a": 1}\nmore chatter\n{"status": "pass"}\n'
    assert parse_stdout_json(out) == {"status": "pass"}
    assert parse_stdout_json('{"x": 1}\r\n') == {"x": 1}


@pytest.mark.parametrize("bad", ["", "   \n", "no json here\n", '{"status": "pass", "log": "cut off'])
def test_unusable_stdout_is_an_error(bad: str) -> None:
    with pytest.raises(SandboxOutputError):
        parse_stdout_json(bad)


def test_a_truncated_last_line_never_falls_back_to_an_older_object() -> None:
    with pytest.raises(SandboxOutputError):
        parse_stdout_json('{"status": "old"}\n{"status": "pass", "lo')


# ---------- verify mapping ----------
def test_verify_pass_requires_the_full_vector_count() -> None:
    assert parse_verify(dict(VERIFY_PASS), expected_total=16).status == "pass"
    short = json.loads(json.dumps(VERIFY_PASS))
    short["sim"]["total"] = 12
    short["sim"]["passed"] = 12
    result = parse_verify(short, expected_total=16)
    assert result.status == "sim_fail" and not result.passed_all(16)
    verification = result.to_verification(16)
    assert verification.passed < verification.total and verification.failures[0].name == "vector_count_mismatch"
    no_sim = {k: v for k, v in VERIFY_PASS.items() if k != "sim"}
    assert parse_verify(no_sim, expected_total=16).status == "sim_error"


def test_failure_statuses_map_to_the_wire_verification() -> None:
    contract = VerifyResult.model_validate(
        {"status": "contract_fail", "contract": {"ok": False, "errors": ["port acc: width 16, expected 22"], "ports": []}, "duration_ms": 5}
    )
    v = contract.to_verification(16)
    assert v.lint == "fail" and "width 16, expected 22" in (v.lint_log or "") and v.passed == 0 and v.tool == "Yosys interface check"

    lint = VerifyResult.model_validate({"status": "lint_fail", "lint": {"ok": False, "errors": 2, "log": "%Error: x"}})
    assert lint.to_verification(16).lint_log == "%Error: x"

    timeout = VerifyResult.model_validate({"status": "timeout", "message": "slow"})
    tv = timeout.to_verification(16, timeout_s=60)
    assert tv.lint == "pass" and tv.failures[0].name == "simulation_timeout" and "60 s" in tv.failures[0].expected

    failing = VerifyResult.model_validate(
        {
            "status": "sim_fail",
            "lint": {"ok": True},
            "sim": {
                "ok": False, "total": 16, "passed": 13, "failure_count": 3,
                "failures": [{"name": "c", "inputs": "i", "expected": "0x1", "got": "0x0", "pass": False, "cycle": 16}],
            },
        }
    )
    fv = failing.to_verification(16)
    assert (fv.passed, fv.total, fv.failures[0].cycle, fv.failures[0].pass_) == (13, 16, 16, False)


# ---------- measure and eqy mapping ----------
def test_measure_ok_maps_to_ppa_without_power() -> None:
    result = parse_measure(dict(MEASURE_OK))
    assert result.status == "ok" and result.measured
    ppa = result.to_ppa()
    assert (ppa.area_um2, ppa.cell_count, ppa.clock_period_ns, ppa.power_mw) == (1840.2, 412, 3.2, None)
    assert ppa.pdk == "Nangate45" and "OpenSTA" in ppa.tools


def test_measure_edge_cases() -> None:
    no_area = json.loads(json.dumps(MEASURE_OK))
    no_area["synth"]["area_um2"] = -5
    assert parse_measure(no_area).status == "synth_fail"

    no_sta = json.loads(json.dumps(MEASURE_OK))
    no_sta["sta"] = {"ok": False, "clock_period_ns": None, "log": "no paths"}
    kept = parse_measure(no_sta)
    assert kept.status == "sta_fail" and kept.measured and kept.to_ppa().clock_period_ns is None

    bad_circuit = json.loads(json.dumps(MEASURE_OK))
    bad_circuit["circuit"] = {
        "format": "verity.circuit/1", "top": "t",
        "nodes": [{"id": "a", "kind": "add", "label": "add", "width": 8, "module": "t"}],
        "edges": [{"source": "a", "target": "ghost", "width": 8}],
    }
    assert parse_measure(bad_circuit).circuit is None  # inconsistent graph is dropped, the PPA survives


def test_eqy_timeout_and_error_never_count_as_proven() -> None:
    for status in ("timeout", "error", "inconclusive"):
        assert EquivalenceResult(status=status).to_equivalence("c1").status == "inconclusive"  # type: ignore[arg-type]
    assert parse_eqy({"status": "proven", "log": "PASS"}).to_equivalence("c1").status == "proven"
    assert parse_eqy({"status": "not_equivalent"}).to_equivalence("c1").status == "not_equivalent"


# ---------- runner logic ----------
class ScriptedRunner(SandboxRunner):
    def __init__(self, settings, outputs: list) -> None:
        super().__init__(settings, max_parallel=2)
        self.outputs = list(outputs)
        self.calls: list[SandboxCall] = []

    async def _execute(self, call: SandboxCall) -> RawOutput:
        self.calls.append(call)
        item = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def image_digest(self) -> str:
        return "sha256:scripted"


def out(payload: dict | str, exit_code: int | None = 0, **kw) -> RawOutput:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return RawOutput(exit_code=exit_code, stdout=text + "\n", stderr=kw.pop("stderr", ""), duration_ms=kw.pop("duration_ms", 42), **kw)


@pytest.fixture()
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "RETRY_PAUSE_S", 0.0)


@pytest.fixture()
def rtl(mac4: TaskPack):
    return mac4.validate_rtl(mac4.reference_rtl() or "")


async def test_exit_zero_is_parsed_and_the_call_uses_fixed_script_and_env(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out(VERIFY_PASS)])
    result = await runner.verify(rtl, mac4)
    assert result.passed_all(mac4.test_count)
    call = runner.calls[0]
    assert call.script == "verify" and dict(call.work_files) == {"dut.v": rtl.data}
    assert call.env["VERITY_SIM_TIMEOUT_S"] == str(settings.timeouts.sim_s)
    assert call.timeout_s == settings.timeouts.verify_s + settings.timeouts.sandbox_overhead_s
    assert result.duration_ms == 10  # the script's own duration is kept


@pytest.mark.usefixtures("fast_retries")
async def test_exit_three_is_retried_once_then_raised(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out("", 3, stderr="tool missing")])
    with pytest.raises(ToolInfraError) as err:
        await runner.verify(rtl, mac4)
    assert len(runner.calls) == settings.tools.infra_retries + 1
    assert not isinstance(err.value, FatalToolError) and err.value.exit_code == 3


@pytest.mark.usefixtures("fast_retries")
async def test_a_retry_can_recover(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out("", 3), out(VERIFY_PASS)])
    assert (await runner.verify(rtl, mac4)).status == "pass"
    assert len(runner.calls) == 2


async def test_input_errors_are_fatal_and_not_retried(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out({"status": "input_error", "message": "no dut.v"}, 2)])
    with pytest.raises(FatalToolError):
        await runner.verify(rtl, mac4)
    assert len(runner.calls) == 1
    runner = ScriptedRunner(settings, [out({"status": "input_error", "message": "bad toml"}, 0)])
    with pytest.raises(FatalToolError):
        await runner.verify(rtl, mac4)


async def test_outer_timeout_becomes_a_result_not_an_exception(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [RawOutput(exit_code=None, stdout="", stderr="", duration_ms=99_000, timed_out=True)])
    verify = await runner.verify(rtl, mac4)
    assert verify.status == "timeout" and not verify.passed_all(mac4.test_count)
    measure = await runner.measure(rtl, mac4)
    assert measure.status == "timeout" and not measure.measured
    eqy = await runner.check_equivalence(rtl, rtl, mac4)
    assert eqy.status == "timeout" and eqy.to_equivalence("c1").status == "inconclusive"
    assert {c.script for c in runner.calls} == {"verify", "measure", "eqy"}
    assert dict(runner.calls[-1].work_files).keys() == {"dut.v", "b.v"}  # gold as dut.v, gate as b.v


@pytest.mark.usefixtures("fast_retries")
async def test_unparseable_output_is_an_infrastructure_error(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out("this is not json")])
    with pytest.raises(ToolInfraError):
        await runner.verify(rtl, mac4)
    runner = ScriptedRunner(settings, [out({"schema": "verity.verify/1", "status": "banana"})])
    with pytest.raises(ToolInfraError):
        await runner.verify(rtl, mac4)


async def test_measure_is_cached_per_pack_and_rtl(settings, mac4, rtl) -> None:
    runner = ScriptedRunner(settings, [out(MEASURE_OK)])
    first = await runner.measure(rtl, mac4)
    second = await runner.measure(rtl, mac4)
    assert len(runner.calls) == 1 and first == second and first is not second
    timeout_runner = ScriptedRunner(settings, [RawOutput(exit_code=None, stdout="", stderr="", duration_ms=1, timed_out=True)])
    await timeout_runner.measure(rtl, mac4)
    await timeout_runner.measure(rtl, mac4)
    assert len(timeout_runner.calls) == 2, "a timeout must not be cached"


# ---------- local docker command line ----------
def test_docker_command_is_a_fixed_hardened_template(settings, mac4, tmp_path: Path) -> None:
    runner = LocalDockerRunner(settings)
    work, pack_dir = tmp_path / "work", tmp_path / "pack"
    call = SandboxCall(script="verify", work_files={}, pack=mac4, env=runner._env, timeout_s=75)
    cmd = runner._command("verity-abc123", work, pack_dir, call)
    assert isinstance(cmd, list)
    joined = " ".join(cmd)
    for needle in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 1000:1000", "--pull never"):
        assert needle in joined, needle
    assert f"type=bind,source={work},target=/work" in cmd
    assert f"type=bind,source={pack_dir},target=/pack,readonly" in cmd
    assert cmd[-2:] == [settings.tools.image, "/verity/bin/verify"]
    assert "VERITY_PACK_DIR=/pack" in cmd and "VERITY_WORK_DIR=/work" in cmd


def test_docker_command_refuses_a_comma_in_a_mount_path(settings, mac4, tmp_path: Path) -> None:
    runner = LocalDockerRunner(settings)
    call = SandboxCall(script="measure", work_files={}, pack=mac4, env=runner._env, timeout_s=10)
    with pytest.raises(FatalToolError):
        runner._command("n", tmp_path / "a,b", tmp_path / "pack", call)


def test_docker_user_can_be_switched_off_for_docker_desktop(settings, mac4, tmp_path: Path) -> None:
    tools = settings.tools.model_copy(update={"docker_user": ""})
    runner = LocalDockerRunner(settings.model_copy(update={"tools": tools}))
    call = SandboxCall(script="verify", work_files={}, pack=mac4, env=runner._env, timeout_s=10)
    cmd = runner._command("n", tmp_path / "work", tmp_path / "pack", call)
    assert "--user" not in cmd and "--cap-drop" in cmd  # every other hardening flag stays


# ---------- sandbox check behind GET /health and POST /runs (no Docker: run_process is replaced) ----------
class FakeDocker:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    async def __call__(self, cmd, *, timeout_s, cap):  # noqa: ANN001, ANN204
        self.calls.append(list(cmd))
        item = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(item, BaseException):
            raise item
        if item == "hang":
            await asyncio.sleep(60)
        return item


def proc(exit_code: int | None, stderr: str = "", timed_out: bool = False) -> ProcessResult:
    return ProcessResult(exit_code=exit_code, stdout=b"sha256:abc\n" if exit_code == 0 else b"", stderr=stderr.encode(), timed_out=timed_out)


NO_IMAGE = "Error response from daemon: No such image: verity-tools:0.1"
NO_DAEMON = (
    "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct "
    "and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (proc(0), None),
        (proc(1, NO_IMAGE), cap.IMAGE_MISSING),
        (proc(1, NO_DAEMON), cap.DOCKER_DOWN),
        (proc(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"), cap.DOCKER_DOWN),
        (proc(None, timed_out=True), cap.DOCKER_DOWN),
        (FileNotFoundError("docker"), cap.DOCKER_DOWN),
    ],
)
async def test_sandbox_check_reads_docker_and_the_configured_image(settings, monkeypatch, result, expected) -> None:
    docker = FakeDocker(result)
    monkeypatch.setattr(local_docker, "run_process", docker)
    runner = LocalDockerRunner(settings)
    assert await runner.sandbox_problem() == expected
    assert docker.calls == [["docker", "image", "inspect", "--format", "{{.Id}}", settings.tools.image]]


async def test_sandbox_check_is_cached_then_refreshed(settings, monkeypatch) -> None:
    docker = FakeDocker(proc(1, NO_DAEMON), proc(0))
    monkeypatch.setattr(local_docker, "run_process", docker)
    runner = LocalDockerRunner(settings)
    results = await asyncio.gather(*(runner.sandbox_problem() for _ in range(5)))
    assert results == [cap.DOCKER_DOWN] * 5 and len(docker.calls) == 1  # one docker call answers them all

    monkeypatch.setattr(local_docker, "PROBE_TTL_S", 0.0)  # the cache has aged out: Docker is back
    assert await runner.sandbox_problem() is None and len(docker.calls) == 2


@pytest.mark.usefixtures("fast_retries")
async def test_a_failed_sandbox_call_makes_the_next_check_look_at_docker_again(settings, mac4, rtl, monkeypatch) -> None:
    docker = FakeDocker(proc(0), proc(1, NO_IMAGE))
    monkeypatch.setattr(local_docker, "run_process", docker)
    runner = LocalDockerRunner(settings)
    assert await runner.sandbox_problem() is None  # cached as fine

    async def broken(call: SandboxCall) -> RawOutput:
        raise ToolInfraError("docker call failed", script=call.script)

    monkeypatch.setattr(runner, "_execute", broken)
    with pytest.raises(ToolInfraError):
        await runner.verify(rtl, mac4)
    assert await runner.sandbox_problem() == cap.IMAGE_MISSING and len(docker.calls) == 2


async def test_a_hung_docker_counts_as_down_within_the_time_limit(settings, monkeypatch) -> None:
    monkeypatch.setattr(local_docker, "run_process", FakeDocker("hang"))
    monkeypatch.setattr(local_docker, "PROBE_TIMEOUT_S", 0.05)
    runner = LocalDockerRunner(settings)
    started = time.monotonic()
    assert await runner.sandbox_problem() == cap.DOCKER_DOWN
    assert time.monotonic() - started < 3


async def test_backends_without_a_cheap_check_report_no_problem(settings) -> None:
    assert await ScriptedRunner(settings, [out(VERIFY_PASS)]).sandbox_problem() is None
