"""sandbox/lib/verity_wave.py (runs inside the tool image; standard library only, so it is tested here directly):
VCD parsing, sampling on the testbench edge, the golden replay and its safety check, the window, the fallback."""

import importlib.util
import sys
from pathlib import Path

import pytest

from verity.config import AGENT_ROOT

sys.path.insert(0, str(AGENT_ROOT / "sandbox" / "lib"))
import verity_wave as vw  # noqa: E402

PERIOD_PS = 10_000  # 10 ns clock, starting low: rising at 5 ns, falling at 10 ns, ...


def load_golden(pack: str):
    path = AGENT_ROOT / "tasks" / pack / "golden.py"
    spec = importlib.util.spec_from_file_location(f"golden_{pack}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def vcd(top: str, variables: list[tuple[str, int, str]], changes: dict[int, dict[str, str]]) -> str:
    """Icarus-style VCD text. variables: (name, width, kind). changes: time_ps -> {name: bits}."""
    codes = {name: chr(33 + i) for i, (name, _w, _k) in enumerate(variables)}
    lines = ["$date today $end", "$timescale", "\t1ps", "$end", f"$scope module {top} $end"]
    for name, width, kind in variables:
        rng = f" [{width - 1}:0]" if width > 1 else ""
        lines.append(f"$var {kind} {width} {codes[name]} {name}{rng} $end")
    lines += ["$upscope $end", "$enddefinitions $end"]
    widths = {name: width for name, width, _k in variables}
    for t in sorted(changes):
        lines.append(f"#{t}")
        for name, bits in changes[t].items():
            lines.append(f"{bits}{codes[name]}" if widths[name] == 1 else f"b{bits} {codes[name]}")
    return "\n".join(lines) + "\n"


def counter_vcd(steps: int, *, jump_at: int | None) -> str:
    """cnt8 with en high: reset for the first 3 rising edges, then counting; the design adds 2 at `jump_at`."""
    variables = [("clk", 1, "wire"), ("en", 1, "wire"), ("rst_n", 1, "wire"), ("q", 8, "reg"), ("hits", 4, "reg"), ("nxt", 8, "reg")]
    changes: dict[int, dict[str, str]] = {0: {"clk": "0", "en": "1", "rst_n": "0", "q": "x", "hits": "0", "nxt": "x"}}
    q, hits = None, 0
    for k in range(steps):
        rise, fall = k * PERIOD_PS + PERIOD_PS // 2, (k + 1) * PERIOD_PS
        rst_n = 0 if k < 3 else 1
        if rst_n == 0:
            q = 0
        else:
            q = (q + (2 if q == jump_at else 1)) & 0xFF
            hits = (hits + 1) & 0xF
        changes[rise] = {"clk": "1", "q": format(q, "b"), "hits": format(hits, "b"), "nxt": format((q + 1) & 0xFF, "b")}
        changes[fall] = {"clk": "0"}
        if k == 2:
            changes[fall]["rst_n"] = "1"
    return vcd("cnt8", variables, changes)


def cnt_task() -> dict:
    return {
        "top": "cnt8",
        "clock": {"name": "clk"},
        "reset": {"name": "rst_n", "active_low": True, "synchronous": True},
        "ports": [
            {"name": "clk", "dir": "in", "width": 1},
            {"name": "rst_n", "dir": "in", "width": 1},
            {"name": "en", "dir": "in", "width": 1},
            {"name": "q", "dir": "out", "width": 8},
        ],
    }


def test_vcd_values_and_formatting() -> None:
    text = counter_vcd(6, jump_at=None)
    parsed = vw.parse_vcd(text, "cnt8")
    assert parsed.unit_ps == 1.0 and [v.name for v in parsed.vars] == ["clk", "en", "rst_n", "q", "hits", "nxt"]
    q = parsed.var("q")
    assert q is not None and q.width == 8
    assert vw.fit_bits(parsed.value_at(q.code, 0), 8) == "xxxxxxxx"
    assert vw.int_of(vw.fit_bits(parsed.value_at(q.code, 4 * PERIOD_PS + 6_000), 8)) == 2
    assert vw.fit_bits("1", 4) == "0001" and vw.fit_bits("z1", 4) == "zzz1"
    assert vw.fmt_value(5, 8) == "0x05" and vw.fmt_value(1, 1) == "1" and vw.fmt_value(None, 8) == "x"
    assert vw.fmt_value(0x3F8040, 22) == "0x3F8040"


def test_a_failing_counter_gets_a_window_with_golden_expected_values() -> None:
    # Case runs from t=0; the test fails at the falling edge after the rising edge where q went 3 -> 5.
    text = counter_vcd(12, jump_at=3)
    parsed = vw.parse_vcd(text, "cnt8")
    fail_ns = 7 * PERIOD_PS / 1000  # falling edge at 70 ns (sample 6): q got 5, expected 4
    trace = {
        "target": "count_up",
        "cases": [{"name": "count_up", "start_ns": 0.0, "end_ns": fail_ns, "ok": False, "expected": "q=0x04", "got": "q=0x05", "cycle": 4}],
        "stop_ns": fail_ns + 4 * PERIOD_PS / 1000,
    }
    wave = vw.build_wave(parsed, cnt_task(), trace, load_golden("cnt8"))
    assert wave is not None
    assert wave["case"] == "count_up" and wave["fail_cycle"] == 4 and wave["start_cycle"] == 0
    rows = {s["name"]: s for s in wave["signals"]}
    assert list(rows)[:3] == ["rst_n", "en", "q"], "ports first, in task.toml order, never the clock"
    assert "clk" not in rows
    q = rows["q"]
    fail_col = wave["fail_cycle"] - wave["start_cycle"]
    assert q["got"][fail_col] == "0x05" and q["expected"][fail_col] == "0x04"
    assert q["got"][:fail_col] == q["expected"][:fail_col], "no difference before the failure"
    assert len(q["got"]) == len(q["expected"]) == fail_col + 1 + 4, "4 cycles after the failure"
    assert "hits" in rows and "expected" not in rows["hits"], "a register of the design, no expectation"
    assert "golden model" in wave["note"] and "hold the last inputs" in wave["note"]


def test_a_replay_that_disagrees_with_the_test_is_not_shown() -> None:
    # The design is correct, but the trace says the test failed at cycle 4: the replay finds no difference there, so
    # it is not trusted; the expected value comes only from the failing check's own text, at the failing cycle.
    parsed = vw.parse_vcd(counter_vcd(10, jump_at=None), "cnt8")
    fail_ns = 7 * PERIOD_PS / 1000
    trace = {
        "target": "count_up",
        "cases": [{"name": "count_up", "start_ns": 0.0, "end_ns": fail_ns, "ok": False, "expected": "q=0x09", "got": "q=0x04", "cycle": 4}],
        "stop_ns": fail_ns,
    }
    wave = vw.build_wave(parsed, cnt_task(), trace, load_golden("cnt8"))
    assert wave is not None
    q = next(s for s in wave["signals"] if s["name"] == "q")
    assert q["expected"][-1] == "0x09" and set(q["expected"][:-1]) == {""}
    assert "only where the failing check names them" in wave["note"]


def test_no_golden_and_no_named_value_means_no_expected_row() -> None:
    parsed = vw.parse_vcd(counter_vcd(8, jump_at=3), "cnt8")
    trace = {
        "target": "c",
        "cases": [{"name": "c", "start_ns": 0.0, "end_ns": 70.0, "ok": False, "expected": "done at cycle 16", "got": "x", "cycle": None}],
    }
    wave = vw.build_wave(parsed, cnt_task(), trace, None)
    assert wave is not None and all("expected" not in s for s in wave["signals"])
    assert "not available" in wave["note"]
    assert wave["fail_cycle"] == 6, "without a test cycle the failure is labelled by its sample index"


def test_a_passing_case_shows_its_start() -> None:
    parsed = vw.parse_vcd(counter_vcd(20, jump_at=None), "cnt8")
    trace = {"target": "c", "cases": [{"name": "c", "start_ns": 0.0, "end_ns": 200.0, "ok": True, "expected": "", "got": "", "cycle": None}]}
    wave = vw.build_wave(parsed, cnt_task(), trace, load_golden("cnt8"), first_n=12)
    assert wave is not None and wave["fail_cycle"] is None and wave["start_cycle"] == 0
    q = next(s for s in wave["signals"] if s["name"] == "q")
    assert len(q["got"]) == len(q["expected"]) == 12
    assert all(e in ("", g) for g, e in zip(q["got"], q["expected"])), "a passing case never shows a mismatch"
    assert q["expected"][0] == "" and q["expected"][-1] == q["got"][-1], "unknown before the first reset edge"


def test_a_combinational_design_is_sampled_on_the_one_ns_grid() -> None:
    # Two equal vectors in a row leave no change in the VCD; the grid still gives one sample per vector.
    variables = [("a", 8, "wire"), ("b", 8, "wire"), ("cin", 1, "wire"), ("cout", 1, "wire"), ("sum", 8, "wire")]
    changes = {
        0: {"a": "0", "b": "0", "cin": "0", "cout": "0", "sum": "0"},
        1000: {"a": "1", "b": "1", "sum": "10"},  # 1 + 1 = 2
        2000: {},  # the same vector again: no change in the VCD
        3000: {"a": "11111111", "b": "1", "sum": "0"},  # 255 + 1 = 256: cout stuck at 0 (the bug the test finds)
    }
    text = vcd("add8", variables, changes)
    task = {
        "top": "add8",
        "clock": {"name": ""},
        "reset": {"name": ""},
        "ports": [
            {"name": "a", "dir": "in", "width": 8},
            {"name": "b", "dir": "in", "width": 8},
            {"name": "cin", "dir": "in", "width": 1},
            {"name": "sum", "dir": "out", "width": 8},
            {"name": "cout", "dir": "out", "width": 1},
        ],
    }
    trace = {"target": "v", "cases": [{"name": "v", "start_ns": 1.0, "end_ns": 4.0, "ok": False, "expected": "", "got": "", "cycle": 3}]}
    wave = vw.build_wave(vw.parse_vcd(text, "add8"), task, trace, load_golden("add8"))
    assert wave is not None
    rows = {s["name"]: s for s in wave["signals"]}
    assert rows["a"]["got"] == ["0x01", "0x01", "0xFF"], "the repeated vector at 2 ns is a sample too"
    assert rows["cout"]["expected"] == ["0", "0", "1"] and rows["cout"]["got"] == ["0", "0", "0"]
    assert rows["sum"]["expected"] == ["0x02", "0x02", "0x00"]
    assert wave["fail_cycle"] == 3 and wave["start_cycle"] == 1


def test_expected_text_parsing_only_takes_output_ports() -> None:
    assert vw.expected_from_check("sum=0x1F cout=1 a=0x03", {"sum": 8, "cout": 1}) == {"sum": 31, "cout": 1}
    assert vw.expected_from_check("0x3F8040", {"acc": 22}) == {}


@pytest.mark.parametrize("pack", ["cnt8", "fsm_seq", "add8", "mac4"])
def test_every_live_family_has_a_replay(pack: str) -> None:
    golden = load_golden(pack)
    assert any(all(hasattr(golden, a) for a in attrs) for attrs, _step in vw.STEPPERS)
