"""The repair prompt's cycle table and the optimize prompt's measured evidence (prompt versions repair.v4, optimize.v4)."""

import pytest

from verity.contracts import PPA, TestCase, Verification
from verity.core.llm_types import LLMResponse
from verity.core.pack import TaskPack
from verity.core.results import CriticalPath, MeasureResult, SynthResult, TimingResult, WaveSignal, WaveTrace
from verity.roles.optimizer import optimize
from verity.roles.repair import cycle_table, repair


class ScriptedGateway:
    """Implements LLMGateway: returns one fixed answer, records every call."""

    def __init__(self, settings, texts: list[str]) -> None:
        self.settings = settings
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def call(self, role, *, system, user, response_format="text", prompt_version="", tags=None):
        self.calls.append({"role": role, "system": system, "user": user, "tags": dict(tags or {})})
        response = LLMResponse(text=self.texts[0], input_tokens=100, output_tokens=50, model="m", request_id="r", latency_ms=5)
        return response, 0.001

FIXED = "DIAGNOSIS: q skipped a value.\nREASON: add one.\n```verilog\nmodule mac4 (input clk); endmodule\n```"
CHILD = "REASON: share the adder tree.\n```verilog\nmodule mac4 (input clk); endmodule\n```"

FAILING = Verification(
    lint="pass",
    passed=9,
    total=10,
    failures=[TestCase(name="count_up", inputs="en high", expected="q=0x04", got="q=0x05", pass_=False, cycle=4)],
    tool="sim",
    duration_ms=1,
)
TRACE = WaveTrace(
    case="count_up",
    fail_cycle=4,
    start_cycle=1,
    signals=[
        WaveSignal(name="en", width=1, got=["1", "1", "1", "1", "1"]),
        WaveSignal(name="q", width=8, got=["0x01", "0x02", "0x03", "0x05", "0x06"], expected=["0x01", "0x02", "0x03", "0x04", "0x05"]),
        WaveSignal(name="acc", width=22, got=["x", "x", "x", "x", "x"], expected=["", "", "", "", "0x000001"]),
    ],
    note="Expected values come from the golden model, replayed on the same inputs.",
)


def test_cycle_table_marks_the_failing_cycle_and_aligns_columns() -> None:
    table = cycle_table(TRACE)
    lines = table.splitlines()
    assert lines[0].startswith("Cycle table of count_up around the first mismatch (cycle 4, marked *)")
    header = lines[1].split()
    assert header == ["cycle", "1", "2", "3", "4*", "5"]
    rows = {line.split("  ")[0].strip(): line for line in lines[2:-1]}
    assert rows["q expected"].split()[-2:] == ["0x04", "0x05"] and rows["q"].split()[-2:] == ["0x05", "0x06"]
    assert rows["acc expected"].split()[2:] == [".", ".", ".", ".", "0x000001"], "'.' where the test expects nothing"
    widths = {len(line) for line in lines[1:-1]}
    assert len(widths) <= 3, "columns line up"
    assert lines[-1] == TRACE.note


async def test_repair_prompt_carries_the_cycle_table(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, [FIXED])
    out = await repair(gateway, mac4, "module mac4; endmodule", FAILING, attempt_n=2, waves=TRACE)
    user = gateway.calls[0]["user"]
    assert "- count_up at cycle 4" in user and "Cycle table of count_up" in user and "4*" in user
    assert out.meta.prompt_version.startswith("repair.v4.md@")
    assert "cycle table" in gateway.calls[0]["system"]


async def test_no_table_without_a_waveform_or_for_a_lint_failure(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, [FIXED])
    await repair(gateway, mac4, "module mac4; endmodule", FAILING, attempt_n=2)
    assert "Cycle table" not in gateway.calls[0]["user"]
    lint = Verification(lint="fail", lint_log="%Error: dut.v:3: syntax error", passed=0, total=10, tool="Verilator", duration_ms=1)
    gateway = ScriptedGateway(settings, [FIXED])
    await repair(gateway, mac4, "module mac4; endmodule", lint, attempt_n=2, waves=TRACE)
    assert "Cycle table" not in gateway.calls[0]["user"]


def _measure() -> MeasureResult:
    return MeasureResult(
        status="ok",
        synth=SynthResult(
            ok=True,
            area_um2=2108.6,
            cell_count=1778,
            cells={"XNOR2_X1": 254, "XOR2_X1": 214, "NAND2_X1": 275, "NOR2_X1": 245, "AOI21_X1": 162, "DFF_X1": 28, "INV_X1": 91},
            cell_area={"XNOR2_X1": 405.4, "XOR2_X1": 341.5, "NAND2_X1": 219.5, "NOR2_X1": 195.5, "AOI21_X1": 172.4, "DFF_X1": 126.6, "INV_X1": 48.4},
        ),
        sta=TimingResult(
            ok=True,
            clock_period_ns=1.594,
            wns_ns=8.406,
            target_period_ns=10.0,
            path=CriticalPath(startpoint="acc[3]", endpoint="acc[20]", start_kind="register", end_kind="register", depth=32, delay_ns=1.556),
        ),
    )


async def test_optimize_prompt_carries_the_parent_evidence(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, [CHILD])
    ppa = PPA(area_um2=2108.6, cell_count=1778, clock_period_ns=1.594, pdk="Nangate45")
    out = await optimize(gateway, mac4, "module mac4; endmodule", ppa, ppa, "area", "SIMPLIFY", parent_measure=_measure())
    user = gateway.calls[0]["user"]
    assert "## Where the parent's area and delay are" in user
    assert "Largest cell types by area: XNOR2_X1 (254 cells, 405.4 um2, 19%)" in user
    line = next(ln for ln in user.splitlines() if ln.startswith("- Largest cell types by area:"))
    assert line.count(" cells, ") == 6, "the top 6 cell types only"
    assert "INV_X1" not in user
    assert "- Flip-flops: 28 (126.6 um2, 6% of the area)" in user
    assert "- Critical path: from register acc[3] to register acc[20], 32 logic stages, 1.556 ns" in user
    assert out.meta.prompt_version.startswith("optimize.v4.md@")
    assert "largest cell types" in gateway.calls[0]["system"]


async def test_optimize_prompt_without_a_measurement_says_so(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, [CHILD])
    ppa = PPA(pdk="Nangate45")
    await optimize(gateway, mac4, "module mac4; endmodule", ppa, ppa, "area", "OPTIMIZE")
    assert "- Not measured." in gateway.calls[0]["user"]
