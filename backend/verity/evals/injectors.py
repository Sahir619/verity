"""Bug injectors for the repair suite (ARCHITECTURE 14.3): known-good reference RTL in, a plausibly buggy copy out.

Four families: wrong width, off-by-one, missing reset, swapped operands. Each injector is a pure text transform on
`reference.v` that returns an `Injection` or None when the family does not apply to that design. The repair suite then
runs the buggy RTL through the sandbox: a bug that still passes the locked tests is reported as `bug_not_detected` and
excluded from the repair statistics (it is an equivalent mutant, not a failure of the repair loop).

The injectors never touch the module header's port names, so a width bug changes a width and nothing else.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..core.pack import TaskPack


@dataclass(frozen=True, slots=True)
class Injection:
    name: str
    text: str
    description: str


def _body_start(text: str) -> int:
    """Offset just after the `);` that closes the module header (0 when it cannot be found)."""
    match = re.search(r"\)\s*;", text)
    return match.end() if match else 0


# ---------- wrong width ----------
_OUT_RANGE = re.compile(r"(\boutput\s+(?:reg\s+|wire\s+|signed\s+)*)\[(\d+):0\]")


def wrong_width(text: str, pack: TaskPack) -> Injection | None:
    """Make the first ranged output one bit narrower. Caught by the interface check, before simulation."""
    del pack
    for match in _OUT_RANGE.finditer(text):
        high = int(match.group(2))
        if high < 1:
            continue
        replaced = f"{match.group(1)}[{high - 1}:0]"
        new = text[: match.start()] + replaced + text[match.end() :]
        return Injection("wrong_width", new, f"output range [{high}:0] narrowed to [{high - 1}:0]")
    return None


# ---------- off by one ----------
_EQ_CONST = re.compile(r"(==\s*\d+'[dD])(\d+)")
_INC_CONST = re.compile(r"(\+\s*\d+'[dD])(1)\b")


def off_by_one(text: str, pack: TaskPack) -> Injection | None:
    """Move the first `== N'dK` comparison to K-1 (a counter or latency bound), else turn a `+ N'd1` into `+ N'd2`."""
    del pack
    start = _body_start(text)
    for match in _EQ_CONST.finditer(text, start):
        value = int(match.group(2))
        if value < 1:
            continue
        new = text[: match.start()] + f"{match.group(1)}{value - 1}" + text[match.end() :]
        return Injection("off_by_one", new, f"comparison constant {value} changed to {value - 1}")
    match = _INC_CONST.search(text, start)
    if match:
        new = text[: match.start()] + f"{match.group(1)}2" + text[match.end() :]
        return Injection("off_by_one", new, "increment of 1 changed to 2")
    return None


# ---------- missing reset ----------
def missing_reset(text: str, pack: TaskPack) -> Injection | None:
    """Disable the first reset branch (`if (!rst_n)` becomes `if (1'b0)`). Registers then start as X in simulation."""
    name = pack.reset.name
    if not name:
        return None
    escaped = re.escape(name)
    patterns = (
        rf"if\s*\(\s*!\s*{escaped}\s*\)" if pack.reset.active_low else rf"if\s*\(\s*{escaped}\s*\)",
        rf"if\s*\(\s*~\s*{escaped}\s*\)" if pack.reset.active_low else rf"if\s*\(\s*{escaped}\s*==\s*1'b1\s*\)",
        rf"if\s*\(\s*{escaped}\s*==\s*1'b0\s*\)" if pack.reset.active_low else rf"if\s*\(\s*{escaped}\s*==\s*1'b1\s*\)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            new = text[: match.start()] + "if (1'b0)" + text[match.end() :]
            return Injection("missing_reset", new, f"reset branch on {name} disabled")
    return None


# ---------- swapped operands ----------
_OPERAND = r"(\{[^{}]*\}|[A-Za-z_]\w*(?:\[[^\]]*\])?)"
_SUBTRACT = re.compile(_OPERAND + r"\s+-\s+" + _OPERAND)


def swapped_operands(text: str, pack: TaskPack) -> Injection | None:
    """Swap the operands of the first subtraction. Without one, swap the first two data inputs on the first body
    line that mentions both (for example a_row and b_row). A swap of commutative operands changes nothing, and the
    suite then reports `bug_not_detected`."""
    start = _body_start(text)
    match = _SUBTRACT.search(text, start)
    if match:
        swapped = f"{match.group(2)} - {match.group(1)}"
        new = text[: match.start()] + swapped + text[match.end() :]
        return Injection("swapped_operands", new, f"subtraction operands swapped: {match.group(0)}")

    clock, reset = pack.clock.name, pack.reset.name
    data_inputs = [p.name for p in pack.ports if p.dir == "in" and p.name not in (clock, reset) and p.bits > 1]
    if len(data_inputs) < 2:
        return None
    first, second = data_inputs[0], data_inputs[1]
    both = re.compile(rf"\b{re.escape(first)}\b")
    other = re.compile(rf"\b{re.escape(second)}\b")
    offset = start
    for line in text[start:].splitlines(keepends=True):
        if both.search(line) and other.search(line):
            marker = "\u0001"
            swapped_line = both.sub(marker, line)
            swapped_line = other.sub(first, swapped_line).replace(marker, second)
            new = text[:offset] + swapped_line + text[offset + len(line) :]
            return Injection("swapped_operands", new, f"{first} and {second} swapped on one line")
        offset += len(line)
    return None


INJECTORS: dict[str, Callable[[str, TaskPack], Injection | None]] = {
    "wrong_width": wrong_width,
    "off_by_one": off_by_one,
    "missing_reset": missing_reset,
    "swapped_operands": swapped_operands,
}


def inject(name: str, text: str, pack: TaskPack) -> Injection | None:
    """Apply one injector. Returns None when the family does not apply or the text did not change."""
    result = INJECTORS[name](text.replace("\r\n", "\n"), pack)
    if result is None or result.text == text:
        return None
    return result
