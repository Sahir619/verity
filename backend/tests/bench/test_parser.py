import copy
import json

import pytest
from pydantic import ValidationError

from verity.contracts.base import to_wire
from verity.core.rtl import Rtl
from verity.tools.base import RawOutput, SandboxRunner
from verity.tools.bench import BENCH_NAMES, BENCH_TIMEOUT_S, parse_bench


def payload():
    return {"schema": "verity.bench/1", "status": "pass", "bench": [
        {"name": name, "instructions": 20, "cycles": 100, "cpi": 999, "passed": True,
         "self_check": 1, "cycles_by_stall": {"busy": 60, "fetch": 30, "data": 10}}
        for name in BENCH_NAMES
    ]}


def test_profile_uses_counter_ratios_and_measured_clock():
    result = parse_bench(payload())
    assert result.status == "pass" and all(row.cpi == 5 for row in result.bench)
    wire = to_wire(result.profile(clock_ns=10, area_by_block_um2={"alu": 12}))
    assert wire["cpi"] == 5 and wire["benchTimeUs"] == 3
    assert wire["cyclesByStall"] == {"busy": 180, "fetch": 90, "data": 30}
    assert wire["areaByBlockUm2"] == {"alu": 12} and wire["criticalPathBlocks"] is None
    assert result.profile().bench_time_us is None and result.profile().clock_ns is None


def test_aggregate_cpi_is_weighted_by_instruction_count():
    data = payload()
    data["bench"][0]["instructions"] = 50
    assert parse_bench(data).profile().cpi == 300 / 90


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "unknown", "order", "check", "zero", "stalls", "retires", "false"])
def test_incomplete_or_inconsistent_results_never_pass(mutation):
    data = payload()
    row = data["bench"][0]
    if mutation == "empty": data["bench"] = []
    elif mutation == "missing": data["bench"].pop()
    elif mutation == "duplicate": data["bench"].append(copy.deepcopy(row))
    elif mutation == "unknown": row["name"] = "invented"
    elif mutation == "order": data["bench"].reverse()
    elif mutation == "check": row["self_check"] = 0
    elif mutation == "zero": row["instructions"] = 0
    elif mutation == "stalls": row["cycles_by_stall"]["busy"] = 0
    elif mutation == "retires": row["instructions"] = 101
    elif mutation == "false": row["passed"] = False
    result = parse_bench(data)
    assert result.status == "fail"
    with pytest.raises(ValueError): result.profile()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_measurements_are_rejected(value):
    data = payload()
    data["bench"][0]["cycles"] = value
    with pytest.raises(ValidationError): parse_bench(data)
    with pytest.raises(ValueError): parse_bench(payload()).profile(clock_ns=value)
    with pytest.raises(ValueError): parse_bench(payload()).profile(area_by_block_um2={"alu": value})


def test_boolean_counter_is_not_an_integer_measurement():
    data = payload()
    data["bench"][0]["instructions"] = True
    with pytest.raises(ValidationError): parse_bench(data)


@pytest.mark.parametrize("status", ["timeout", "compile_error", "sim_error", "fail", "not_supported"])
def test_failure_status_is_preserved(status):
    assert parse_bench({"status": status, "bench": []}).status == status


class ScriptedRunner(SandboxRunner):
    def __init__(self, settings, raw):
        super().__init__(settings, max_parallel=1)
        self.raw = raw
        self.calls = []

    async def _execute(self, call):
        self.calls.append(call)
        return self.raw

    async def image_digest(self):
        return "fixture"


async def test_runner_appends_locked_blocks_and_uses_suite_deadline(settings, packs):
    pack = packs["rv32i_core"]
    rtl = Rtl.validate("module rv32i_core; endmodule", top=pack.top, max_lines=20)
    runner = ScriptedRunner(settings, RawOutput(0, json.dumps(payload()), "", 10))
    result = await runner.bench(rtl, pack)
    call, = runner.calls
    assert result.status == "pass" and call.script == "bench"
    assert call.work_files["dut.v"] == rtl.data + b"\n\n" + (pack.root / "blocks.v").read_bytes()
    assert call.timeout_s == BENCH_TIMEOUT_S + settings.timeouts.sandbox_overhead_s
    assert call.env["VERITY_BENCH_TIMEOUT_S"] == str(BENCH_TIMEOUT_S)
    assert call.env["VERITY_BENCH_WAIT"] == "0"


async def test_outer_timeout_is_a_result(settings, packs):
    pack = packs["rv32i_core"]
    rtl = Rtl.validate("module rv32i_core; endmodule", top=pack.top, max_lines=20)
    runner = ScriptedRunner(settings, RawOutput(None, "", "", 300000, timed_out=True))
    result = await runner.bench(rtl, pack, wait_cycles=3)
    assert result.status == "timeout" and result.bench == [] and len(runner.calls) == 1
    assert result.wait_cycles == 3


def test_wait_cycles_is_parsed_and_defaults_to_fast_memory():
    assert parse_bench(payload()).wait_cycles == 0
    assert parse_bench({**payload(), "wait_cycles": 3}).wait_cycles == 3
    result = parse_bench({"status": "not_supported", "wait_cycles": 3})
    assert result.status == "not_supported" and result.wait_cycles == 3
    with pytest.raises(ValueError):
        result.profile()


@pytest.mark.parametrize("wait", [-1, True, 1.5, "3"])
def test_invalid_wait_cycles_are_rejected(wait):
    with pytest.raises(ValidationError):
        parse_bench({**payload(), "wait_cycles": wait})


async def test_runner_passes_memory_wait_to_sandbox(settings, packs):
    pack = packs["rv32i_core"]
    rtl = pack.validate_rtl(pack.reference_rtl())
    runner = ScriptedRunner(settings, RawOutput(0, json.dumps({**payload(), "wait_cycles": 3}), "", 10))
    result = await runner.bench(rtl, pack, wait_cycles=3)
    assert result.wait_cycles == 3
    assert runner.calls[0].env["VERITY_BENCH_WAIT"] == "3"


@pytest.mark.parametrize("wait", [-1, True, 1.5, "3"])
async def test_runner_rejects_invalid_wait_before_sandbox(settings, packs, wait):
    pack = packs["rv32i_core"]
    runner = ScriptedRunner(settings, RawOutput(0, "", "", 0))
    with pytest.raises(ValueError, match="nonnegative integer"):
        await runner.bench(pack.validate_rtl(pack.reference_rtl()), pack, wait_cycles=wait)
    assert runner.calls == []
