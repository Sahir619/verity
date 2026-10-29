"""Waveform helpers for /verity/bin/wave. Standard library only (no cocotb), so the backend tests can import it.

1. parse_vcd: the top module scope of an Icarus VCD (written by Verity's own dump module, never by the design).
2. Samples on the edge the testbench uses: every falling clock edge (inputs change and outputs are checked there),
   or, for a design with no clock, every 1 ns vector step (verity_tb.settle). A sample is the value at the end of
   that time step.
3. Expected values: the pack's golden model replayed on the sampled inputs, for the families whose golden model is a
   per-cycle function (cnt8, fsm_seq, add8) or a fixed protocol around it (mac4). The replay is checked against the
   test's own verdict; when it disagrees, or for any other pack, expected values are shown only where the failing
   check names them.
4. build_wave: the window of about `before` samples before and `after` samples after the first mismatch.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any, Callable

REG_KINDS = frozenset({"reg", "logic", "bit"})  # VCD var types that can hold a register (never `wire`)
MAX_SIGNAL_WIDTH = 256
COMB_STEP_NS = 1.0  # verity_tb.settle(): a design with no clock is read 1 ns after each vector
_UNIT_PS = {"s": 1e12, "ms": 1e9, "us": 1e6, "ns": 1e3, "ps": 1.0, "fs": 1e-3}
_PAIR = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=(0x[0-9A-Fa-f]+|\d+)\b")

NOTE_REPLAY = "Expected values come from the golden model, replayed on the same inputs."
NOTE_CHECKED = "Expected values are shown only where the failing check names them."
NOTE_NONE = "Expected values are not available for this design."
NOTE_EDGE = "Each cycle is a falling clock edge, where the test drives inputs and checks outputs."
NOTE_COMB = "Each step is one input vector; outputs are read 1 ns after it."
NOTE_AFTER = "The test stops at the first mismatch; the cycles after it hold the last inputs."


# ---------- VCD ----------
@dataclass
class VcdVar:
    name: str
    code: str
    width: int
    kind: str


@dataclass
class Vcd:
    unit_ps: float
    vars: list[VcdVar]
    changes: dict[str, tuple[list[int], list[str]]] = field(default_factory=dict)

    def var(self, name: str) -> VcdVar | None:
        return next((v for v in self.vars if v.name == name), None)

    def value_at(self, code: str, t: int) -> str | None:
        """Bit string (MSB first) at the end of time step t, or None before the first value."""
        times, values = self.changes.get(code, ([], []))
        i = bisect.bisect_right(times, t) - 1
        return values[i] if i >= 0 else None

    def to_units(self, ns: float) -> int:
        return int(round(ns * 1000.0 / self.unit_ps))


def _timescale(text: str) -> float:
    match = re.match(r"^\s*(\d+)\s*([a-z]+)\s*$", text)
    if not match or match.group(2) not in _UNIT_PS:
        return 1.0
    return float(match.group(1)) * _UNIT_PS[match.group(2)]


def parse_vcd(text: str, top: str) -> Vcd:
    """Variables declared directly in the scope `top` (the design's top module) and all their value changes."""
    tokens = text.split()
    i, n = 0, len(tokens)
    scopes: list[str] = []
    unit_ps = 1.0
    found: list[VcdVar] = []
    while i < n:
        tok = tokens[i]
        if tok == "$enddefinitions":
            while i < n and tokens[i] != "$end":
                i += 1
            i += 1
            break
        if tok == "$timescale":
            j = i + 1
            parts: list[str] = []
            while j < n and tokens[j] != "$end":
                parts.append(tokens[j])
                j += 1
            unit_ps = _timescale("".join(parts))
            i = j + 1
        elif tok == "$scope":
            scopes.append(tokens[i + 2] if i + 2 < n else "")
            i += 3
            while i < n and tokens[i - 1] != "$end":
                i += 1
        elif tok == "$upscope":
            if scopes:
                scopes.pop()
            i += 2
        elif tok == "$var":
            j = i + 1
            parts = []
            while j < n and tokens[j] != "$end":
                parts.append(tokens[j])
                j += 1
            if len(parts) >= 4 and scopes == [top]:
                try:
                    width = int(parts[1])
                except ValueError:
                    width = 0
                if width > 0:
                    found.append(VcdVar(name=parts[3], code=parts[2], width=width, kind=parts[0]))
            i = j + 1
        elif tok.startswith("$"):
            j = i + 1
            while j < n and tokens[j] != "$end":
                j += 1
            i = j + 1
        else:
            i += 1

    wanted = {v.code for v in found}
    changes: dict[str, tuple[list[int], list[str]]] = {code: ([], []) for code in wanted}
    t = 0
    while i < n:
        tok = tokens[i]
        first = tok[0]
        if first == "#":
            try:
                t = int(tok[1:])
            except ValueError:
                pass
            i += 1
        elif first in "bB":
            code = tokens[i + 1] if i + 1 < n else ""
            if code in wanted:
                _append(changes[code], t, tok[1:].lower())
            i += 2
        elif first in "rR":
            i += 2
        elif first in "01xXzZ":
            code = tok[1:]
            if code in wanted:
                _append(changes[code], t, first.lower())
            i += 1
        elif tok == "$comment":
            while i < n and tokens[i] != "$end":
                i += 1
            i += 1
        else:
            i += 1
    return Vcd(unit_ps=unit_ps, vars=found, changes=changes)


def _append(store: tuple[list[int], list[str]], t: int, value: str) -> None:
    times, values = store
    if times and times[-1] == t:
        values[-1] = value  # the last change in a time step is the value at the end of it
    else:
        times.append(t)
        values.append(value)


# ---------- values ----------
def fit_bits(raw: str | None, width: int) -> str | None:
    """VCD vectors drop leading zeros (and repeat a leading x or z). Return exactly `width` bits."""
    if raw is None or not raw:
        return None
    if len(raw) < width:
        pad = raw[0] if raw[0] in "xz" else "0"
        raw = pad * (width - len(raw)) + raw
    return raw[-width:]


def int_of(bits: str | None) -> int | None:
    if bits is None or any(ch not in "01" for ch in bits):
        return None
    return int(bits, 2)


def fmt_value(value: int | None, width: int) -> str:
    """'0' or '1' for one bit, '0x0A' (one hex digit per 4 bits, like the testbench) for a vector, 'x' when unknown."""
    if value is None:
        return "x"
    value &= (1 << width) - 1
    if width == 1:
        return str(value)
    digits = max(1, (width + 3) // 4)
    return "0x" + format(value, f"0{digits}X")


def edge_times(vcd: Vcd, var: VcdVar, to_value: str) -> list[int]:
    """Times where a 1-bit signal goes to `to_value` from the opposite level."""
    times, values = vcd.changes.get(var.code, ([], []))
    other = "1" if to_value == "0" else "0"
    out: list[int] = []
    prev: str | None = None
    for t, v in zip(times, values):
        if v == to_value and prev == other:
            out.append(t)
        prev = v
    return out


# ---------- expected values: golden replay ----------
Val = Callable[[str, int], "int | None"]


def _ports(task: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    return [p for p in task.get("ports", []) if p.get("dir") == direction]


def _data_inputs(task: dict[str, Any]) -> list[str]:
    clock = str(task.get("clock", {}).get("name", ""))
    reset = str(task.get("reset", {}).get("name", ""))
    return [p["name"] for p in _ports(task, "in") if p["name"] not in (clock, reset)]


def _reset_low(task: dict[str, Any]) -> str | None:
    reset = task.get("reset", {})
    name = str(reset.get("name", ""))
    return name if name and reset.get("active_low", True) else None


def _step_counter(g: Any, task: dict[str, Any], val: Val, n: int) -> dict[str, list[int | None]] | None:
    outs = _ports(task, "out")
    rst = _reset_low(task)
    if len(outs) != 1 or rst is None:
        return None
    enable = bool(getattr(g, "ENABLE", True))
    en_name = "en" if enable else None
    if en_name and en_name not in _data_inputs(task):
        return None
    exp: list[int | None] = [None] * n
    q: int | None = None
    for i in range(n - 1):
        r = val(rst, i)
        e = val(en_name, i) if en_name else 1
        if r is None or e is None:
            q = None
            continue
        if r == 0:
            q = g.golden_cnt8_step(0, 0, e)
        elif q is None:
            continue
        else:
            q = g.golden_cnt8_step(q, 1, e)
        exp[i + 1] = q
    return {outs[0]["name"]: exp}


def _step_detector(g: Any, task: dict[str, Any], val: Val, n: int) -> dict[str, list[int | None]] | None:
    outs = _ports(task, "out")
    rst = _reset_low(task)
    data = _data_inputs(task)
    if len(outs) != 1 or rst is None or len(data) != 1:
        return None
    exp: list[int | None] = [None] * n
    history: tuple | None = None
    for i in range(n - 1):
        r, d = val(rst, i), val(data[0], i)
        if r is None or d is None:
            history = None
            continue
        if r == 0:
            history, detect = g.golden_fsm_seq_step((), 0, d)
        elif history is None:
            continue
        else:
            history, detect = g.golden_fsm_seq_step(history, 1, d)
        exp[i + 1] = int(detect)
    return {outs[0]["name"]: exp}


def _step_adder(g: Any, task: dict[str, Any], val: Val, n: int) -> dict[str, list[int | None]] | None:
    names = {p["name"] for p in task.get("ports", [])}
    if not {"a", "b", "cin", "sum", "cout"} <= names:
        return None
    total: list[int | None] = [None] * n
    carry: list[int | None] = [None] * n
    for i in range(n):
        a, b, c = val("a", i), val("b", i), val("cin", i)
        if a is None or b is None or c is None:
            continue
        total[i], carry[i] = g.golden_add8(a, b, c)
    return {"sum": total, "cout": carry}


def _step_mac(g: Any, task: dict[str, Any], val: Val, n: int) -> dict[str, list[int | None]] | None:
    """mac4 protocol (task.toml coding_rules): start while idle is edge 0, edges 1..CYCLES each add the lane
    products, done is 0 until edge CYCLES and 1 after it, acc then holds the golden sum until reset; start is ignored
    while running or done; after reset acc and done are 0. acc is only expected where the test checks it (after reset
    and from done on): its value while running is up to the design."""
    names = {p["name"] for p in task.get("ports", [])}
    rst = _reset_low(task)
    if rst is None or not {"start", "a", "b", "acc", "done"} <= names:
        return None
    lanes, width, cycles, mask = int(g.LANES), int(g.LANE_BITS), int(g.CYCLES), int(g.ACC_MASK)
    lane_mask = (1 << width) - 1

    def unpack(flat: int) -> list[int]:
        return [(flat >> (k * width)) & lane_mask for k in range(lanes)]

    acc_exp: list[int | None] = [None] * n
    done_exp: list[int | None] = [None] * n
    phase: str | None = None
    k = 0
    total = 0
    for i in range(n - 1):
        r = val(rst, i)
        if r is None:
            phase = None
            continue
        if r == 0:
            phase, k, total = "idle", 0, 0
            acc_exp[i + 1], done_exp[i + 1] = 0, 0
            continue
        if phase is None:
            continue
        if phase == "idle":
            s = val("start", i)
            if s is None:
                phase = None
                continue
            if s == 1:
                phase, k, total = "run", 0, 0
            done_exp[i + 1] = 0
        elif phase == "run":
            a, b = val("a", i), val("b", i)
            if a is None or b is None:
                phase = None
                continue
            k += 1
            total += sum(g.lane_products(unpack(a), unpack(b)))
            if k >= cycles:
                phase = "done"
                done_exp[i + 1], acc_exp[i + 1] = 1, total & mask
            else:
                done_exp[i + 1] = 0
        else:  # done: hold until reset
            done_exp[i + 1], acc_exp[i + 1] = 1, total & mask
    return {"acc": acc_exp, "done": done_exp}


STEPPERS: tuple[tuple[tuple[str, ...], Callable[..., dict[str, list[int | None]] | None]], ...] = (
    (("golden_cnt8_step",), _step_counter),
    (("golden_fsm_seq_step",), _step_detector),
    (("golden_add8",), _step_adder),
    (("golden_mac4", "lane_products", "LANES", "LANE_BITS", "CYCLES", "ACC_MASK"), _step_mac),
)


def replay(golden: Any, task: dict[str, Any], val: Val, n: int) -> dict[str, list[int | None]] | None:
    if golden is None:
        return None
    for attrs, step in STEPPERS:
        if all(hasattr(golden, a) for a in attrs):
            try:
                return step(golden, task, val, n)
            except Exception:  # noqa: BLE001  (a replay problem only costs the expected row, never the wave)
                return None
    return None


def expected_from_check(text: str, outputs: dict[str, int]) -> dict[str, int]:
    """`name=value` pairs of the failing check's expected text, for output ports only ('q=0x05 cout=1')."""
    found: dict[str, int] = {}
    for name, raw in _PAIR.findall(text or ""):
        if name in outputs:
            found[name] = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    return found


# ---------- the window ----------
def _is_registered(vcd: Vcd, var: VcdVar, rising: set[int]) -> bool:
    times = vcd.changes.get(var.code, ([], []))[0]
    return all(t in rising for t in times if t > 0)


def build_wave(
    vcd: Vcd,
    task: dict[str, Any],
    trace: dict[str, Any],
    golden: Any = None,
    *,
    before: int = 4,
    after: int = 4,
    first_n: int = 12,
    max_regs: int = 4,
) -> dict[str, Any] | None:
    """The wave payload (snake_case, schema of CONTRACTS 3.11) for the target case of `trace`, or None."""
    case = next((c for c in trace.get("cases", []) if c.get("name") == trace.get("target")), None)
    if case is None:
        return None
    clock = str(task.get("clock", {}).get("name", ""))
    clk = vcd.var(clock) if clock else None
    start = vcd.to_units(float(case["start_ns"]))
    end = vcd.to_units(float(case["end_ns"]))
    stop = vcd.to_units(float(trace.get("stop_ns", case["end_ns"])))

    port_names = [p["name"] for p in task.get("ports", [])]
    out_widths = {p["name"]: int(p["width"]) * int(p.get("count", 1)) for p in _ports(task, "out")}

    if clk is not None:
        samples = [t for t in edge_times(vcd, clk, "0") if start < t <= stop]
        rising = set(edge_times(vcd, clk, "1"))
    else:
        # verity_tb convention for a design with no clock: drive one vector, read it 1 ns later, drive the next. A
        # vector equal to the one before leaves no trace in the VCD, so sample the 1 ns grid, not the changes.
        step = max(1, vcd.to_units(COMB_STEP_NS))
        samples = list(range(start, end, step))
        rising = set()
    n = len(samples)
    if n == 0:
        return None

    def bits(name: str, i: int) -> str | None:
        var = vcd.var(name)
        return None if var is None else fit_bits(vcd.value_at(var.code, samples[i]), var.width)

    def val(name: str | None, i: int) -> int | None:
        return None if name is None else int_of(bits(name, i))

    failing = not bool(case.get("ok"))
    if failing:
        fail_idx = max(0, bisect.bisect_right(samples, end) - 1)
        cycle = case.get("cycle")
        anchor = int(cycle) if isinstance(cycle, int) and cycle >= 0 else fail_idx
        offset = anchor - fail_idx
        lo = max(0, fail_idx - before, -offset)
        hi = min(n - 1, fail_idx + after)
    else:
        fail_idx, anchor, offset = -1, None, 0
        lo, hi = 0, min(n, first_n) - 1
    window = range(lo, hi + 1)

    # Signals: the ports in task.toml order (not the clock), then a few registers of the design that change.
    chosen: list[VcdVar] = []
    for name in port_names:
        var = vcd.var(name)
        if var is not None and name != clock:
            chosen.append(var)
    internals = [
        v for v in vcd.vars
        if clk is not None and v.name not in port_names and v.kind in REG_KINDS
        and v.width <= MAX_SIGNAL_WIDTH and not v.name.startswith("_") and _is_registered(vcd, v, rising)
    ]
    changing = [v for v in internals if len({bits(v.name, i) for i in window}) > 1]
    chosen += (changing + [v for v in internals if v not in changing])[:max_regs]

    got = {v.name: [fmt_value(int_of(bits(v.name, i)), v.width) for i in range(n)] for v in chosen}
    replayed = replay(golden, task, val, n)
    notes: list[str] = []
    expected: dict[str, list[int | None]] = {}
    if replayed:
        trusted = _agrees(replayed, got, out_widths, fail_idx if failing else None, window)
        if trusted:
            expected = {k: v for k, v in replayed.items() if k in out_widths}
            notes.append(NOTE_REPLAY)
    if not expected and failing:
        named = expected_from_check(str(case.get("expected", "")), out_widths)
        if named:
            expected = {name: [value if i == fail_idx else None for i in range(n)] for name, value in named.items()}
            notes.append(NOTE_CHECKED)
    if not expected:
        notes.append(NOTE_NONE)
    notes.append(NOTE_EDGE if clk is not None else NOTE_COMB)
    if failing and clk is not None and hi > fail_idx:
        notes.append(NOTE_AFTER)

    signals: list[dict[str, Any]] = []
    for v in chosen:
        row: dict[str, Any] = {"name": v.name, "width": v.width, "got": [got[v.name][i] for i in window]}
        if v.name in expected:
            row["expected"] = [
                "" if expected[v.name][i] is None else fmt_value(expected[v.name][i], v.width) for i in window
            ]
        signals.append(row)
    return {
        "case": str(case["name"]),
        "fail_cycle": anchor if failing else None,
        "start_cycle": lo + offset,
        "signals": signals,
        "note": " ".join(notes),
    }


def _agrees(
    replayed: dict[str, list[int | None]],
    got: dict[str, list[str]],
    widths: dict[str, int],
    fail_idx: int | None,
    window: range,
) -> bool:
    """Trust the replay only when it matches the test's own verdict: no difference before the failing sample (the test
    passed those checks), and at least one difference at it. For a passing case, no difference anywhere."""
    def differs(i: int) -> bool:
        return any(
            exp[i] is not None and name in got and fmt_value(exp[i], widths[name]) != got[name][i]
            for name, exp in replayed.items()
            if name in widths
        )

    if fail_idx is None:
        return not any(differs(i) for i in window)
    if not any(exp[fail_idx] is not None for name, exp in replayed.items() if name in widths):
        return False
    return differs(fail_idx) and not any(differs(i) for i in window if i < fail_idx)
