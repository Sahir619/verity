"""Planted bugs for the sign-off test-strength check: fixed text rules, no model (CONTRACTS 3.10).

Seven rules, applied to the code after the module header. Comments and strings are masked first (same length), so a
rule never edits a comment and every offset stays valid in the real text. Each mutant changes exactly one place:

  drop_reset      `if (!rst_n)` (the pack's reset, active level) becomes `if (1'b0)`
  invert_enable   an `if (...)` condition that reads a 1-bit input other than clock and reset is inverted
  flip_compare    == and != swap; < becomes >=, > becomes <=, >= becomes <, and <= becomes > inside parentheses
                  (outside them `<=` is a non-blocking assignment and is never touched)
  off_by_one      < and <= swap, > and >= swap (same parenthesis rule); a number compared with == or != gets -1
  flip_operator   a binary + becomes -, - becomes +, & and | swap, ^ becomes &, && and || swap (outside [...]);
                  without it a datapath written as one expression (a + b + cin) has no place for the rules above
  const_plus_one  a number outside [...] gets +1, modulo its size (a 1-bit literal flips)
  swap_operands   `x - y` becomes `y - x`; `c ? a : b` becomes `c ? b : a`

Mutants are taken round robin over the rules (spread over each rule's places, so one long table of constants does not
crowd out the rest), up to the limit. Every mutant must pass the same RTL rules a model's design must pass.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .errors import RtlRejected
from .pack import TaskPack

DEFAULT_LIMIT = 20
RULE_ORDER = (
    "drop_reset", "invert_enable", "flip_compare", "off_by_one", "flip_operator", "const_plus_one", "swap_operands",
)
MAX_ARM_CHARS = 80

_OP = re.compile(r"<<<|>>>|<<|>>|===|!==|==|!=|<=|>=|->|<|>")
_ARITH = re.compile(r"->|\+:|-:|\+\+|--|\+=|-=|&=|\|=|\^=|~&|~\||~\^|\^~|&&|\|\||[+\-&|^]")
_ARITH_SWAP = {"+": "-", "-": "+", "&": "|", "|": "&", "^": "&", "&&": "||", "||": "&&"}
_SIZED = re.compile(r"(?<![\w$'])(\d+)\s*'([sS]?)([bBoOdDhH])\s*([0-9a-fA-F_]+)(?![\w'])")
_PLAIN = re.compile(r"(?<![\w$'.])(\d+)(?![\w'.])")
_IF = re.compile(r"\bif\s*\(")
_OPERAND = r"(\{[^{}]*\}|[A-Za-z_]\w*(?:\[[^\]]*\])?)"
_SUBTRACT = re.compile(r"(?<![\w$\]\)])" + _OPERAND + r"(\s*-\s*)" + _OPERAND + r"(?![\w$\[(])")
_BASES = {"b": 2, "o": 8, "d": 10, "h": 16}
_FLIP = {"==": "!=", "!=": "==", "<": ">=", ">": "<=", ">=": "<", "<=": ">"}
_STRICT = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}


@dataclass(frozen=True, slots=True)
class Mutant:
    id: str  # "m01"
    rule: str
    line: int  # 1-based line of the change
    before: str
    after: str
    text: str  # the whole mutated file


@dataclass(frozen=True, slots=True)
class _Edit:
    rule: str
    start: int
    end: int
    replacement: str


# ---------- masking and depth ----------
def mask(text: str) -> str:
    """Same length as `text`: comments and string literals become spaces, newlines are kept."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            blank(i, j)
            i = j
        elif c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            j = n if end < 0 else end + 2
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


def _depths(code: str) -> tuple[list[int], list[int]]:
    """Parenthesis depth and bracket depth before each character."""
    paren, bracket = [0] * (len(code) + 1), [0] * (len(code) + 1)
    p = b = 0
    for i, ch in enumerate(code):
        paren[i], bracket[i] = p, b
        if ch == "(":
            p += 1
        elif ch == ")":
            p = max(0, p - 1)
        elif ch == "[":
            b += 1
        elif ch == "]":
            b = max(0, b - 1)
    paren[len(code)], bracket[len(code)] = p, b
    return paren, bracket


def _body_start(code: str, top: str) -> int:
    """Offset just after the `);` that closes the top module's header (0 when it cannot be found)."""
    head = re.search(rf"\bmodule\s+{re.escape(top)}\b", code)
    at = head.end() if head else 0
    close = re.search(r"\)\s*;", code[at:])
    return at + close.end() if close else 0


def _balanced(code: str, open_at: int) -> int | None:
    """Index of the parenthesis that closes the one at `open_at`."""
    depth = 0
    for k in range(open_at, len(code)):
        if code[k] == "(":
            depth += 1
        elif code[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    return None


# ---------- literals ----------
def _literal_plus(digits: str, base_char: str, size: int | None, delta: int) -> str | None:
    base = _BASES[base_char.lower()]
    clean = digits.replace("_", "")
    try:
        value = int(clean, base)
    except ValueError:
        return None
    new = value + delta
    if size is not None and size > 0:
        new %= 1 << size
    if new < 0 or new == value:
        return None
    if base == 10:
        return str(new)
    width = len(clean)
    text = format(new, {2: "b", 8: "o", 16: "X" if clean.upper() == clean else "x"}[base])
    return text.zfill(width)


def _number_edits(code: str, start: int, bracket: list[int], rule: str, delta: int, spans: list[tuple[int, int]] | None) -> Iterator[_Edit]:
    """+delta on sized literals and plain decimals outside [...]. With `spans`, only numbers that start in one."""
    taken: list[tuple[int, int]] = []
    for m in _SIZED.finditer(code, start):
        taken.append((m.start(), m.end()))
        if bracket[m.start()] > 0 or (spans is not None and not any(a <= m.start() < b for a, b in spans)):
            continue
        size = int(m.group(1))
        new_digits = _literal_plus(m.group(4), m.group(3), size, delta)
        if new_digits is None:
            continue
        a, b = m.start(4), m.end(4)
        yield _Edit(rule, a, b, new_digits)
    for m in _PLAIN.finditer(code, start):
        if any(a <= m.start() < b for a, b in taken) or bracket[m.start()] > 0:
            continue
        if spans is not None and not any(a <= m.start() < b for a, b in spans):
            continue
        new = int(m.group(1)) + delta
        if new < 0:
            continue
        yield _Edit(rule, m.start(1), m.end(1), str(new))


# ---------- rules ----------
def _ops(code: str, start: int) -> Iterator[re.Match[str]]:
    return _OP.finditer(code, start)


def _rule_flip(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
    for m in _ops(code, start):
        op = m.group(0)
        if op not in _FLIP or bracket[m.start()] > 0:
            continue
        if op == "<=" and paren[m.start()] == 0:
            continue  # a non-blocking assignment
        yield _Edit("flip_compare", m.start(), m.end(), _FLIP[op])


def _rule_off_by_one(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
    spans: list[tuple[int, int]] = []
    for m in _ops(code, start):
        op = m.group(0)
        if bracket[m.start()] > 0:
            continue
        if op in _STRICT and not (op == "<=" and paren[m.start()] == 0):
            yield _Edit("off_by_one", m.start(), m.end(), _STRICT[op])
        if op in ("==", "!="):
            after = re.match(r"\s*", code[m.end():])
            at = m.end() + (after.end() if after else 0)
            spans.append((at, at + 1))
    yield from _number_edits(code, start, bracket, "off_by_one", -1, spans)


def _rule_operator(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
    del paren
    for m in _ARITH.finditer(code, start):
        op = m.group(0)
        if op not in _ARITH_SWAP or bracket[m.start()] > 0:
            continue
        before = code[start : m.start()].rstrip()
        if not before or not re.search(r"[\w)\]}]$", before):
            continue  # a unary operator (reduction or sign), not a binary one
        yield _Edit("flip_operator", m.start(), m.end(), _ARITH_SWAP[op])


def _rule_constants(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
    del paren
    yield from _number_edits(code, start, bracket, "const_plus_one", +1, None)


def _rule_swap(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
    edits: list[_Edit] = []
    for m in _SUBTRACT.finditer(code, start):
        if bracket[m.start()] > 0 or m.group(1) == m.group(3):
            continue
        edits.append(_Edit("swap_operands", m.start(), m.end(), f"{m.group(3)}{m.group(2)}{m.group(1)}"))
    for q, ch in enumerate(code):
        if q < start or ch != "?" or bracket[q] > 0:
            continue
        swapped = _ternary(code, q)
        if swapped is not None:
            edits.append(swapped)
    yield from sorted(edits, key=lambda e: e.start)


def _ternary(code: str, q: int) -> _Edit | None:
    depth = 0
    colon = end = None
    for k in range(q + 1, len(code)):
        ch = code[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                if colon is not None:
                    end = k
                break
            depth -= 1
        elif ch == ":" and depth == 0 and colon is None:
            colon = k
        elif ch in ";," and depth == 0:
            if colon is not None:
                end = k
            break
        elif ch == "?" and depth == 0:
            return None  # nested ternary: leave it alone
    if colon is None or end is None:
        return None
    first, second = code[q + 1 : colon].strip(), code[colon + 1 : end].strip()
    if not first or not second or first == second or "\n" in first + second:
        return None
    if len(first) > MAX_ARM_CHARS or len(second) > MAX_ARM_CHARS:
        return None
    return _Edit("swap_operands", q, end, f"? {second} : {first}")


def _conditions(code: str, start: int) -> Iterator[tuple[int, int, str]]:
    """(open paren index, close paren index, condition text) of every `if (...)`."""
    for m in _IF.finditer(code, start):
        open_at = m.end() - 1
        close = _balanced(code, open_at)
        if close is not None:
            yield open_at, close, code[open_at + 1 : close]


def _reset_forms(pack: TaskPack) -> set[str]:
    name = pack.reset.name
    if not name:
        return set()
    if pack.reset.active_low:
        return {f"!{name}", f"~{name}", f"{name}==1'b0", f"{name}==0", f"{name}==1'd0", f"!({name})"}
    return {name, f"{name}==1'b1", f"{name}==1", f"{name}==1'd1"}


def _rule_drop_reset(pack: TaskPack) -> Callable[[str, int, list[int], list[int]], Iterator[_Edit]]:
    forms = _reset_forms(pack)

    def rule(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
        for open_at, close, cond in _conditions(code, start):
            if re.sub(r"\s+", "", cond) in forms:
                yield _Edit("drop_reset", open_at + 1, close, "1'b0")

    return rule


def _rule_invert(pack: TaskPack) -> Callable[[str, int, list[int], list[int]], Iterator[_Edit]]:
    controls = [
        p.name for p in pack.ports
        if p.dir == "in" and p.bits == 1 and p.name not in (pack.clock.name, pack.reset.name)
    ]
    forms = _reset_forms(pack)

    def rule(code: str, start: int, paren: list[int], bracket: list[int]) -> Iterator[_Edit]:
        if not controls:
            return
        for open_at, close, cond in _conditions(code, start):
            if re.sub(r"\s+", "", cond) in forms:
                continue
            if any(re.search(rf"\b{re.escape(name)}\b", cond) for name in controls):
                yield _Edit("invert_enable", open_at + 1, close, f"!({cond.strip()})")

    return rule


# ---------- selection ----------
def _spread(n: int) -> list[int]:
    """0, n/2, n/4, 3n/4, n/8, ...: an order that covers the whole list early."""
    seen: set[int] = set()
    out: list[int] = []
    parts = 1
    while len(out) < n:
        for j in range(parts):
            i = int(j * n / parts)
            if i not in seen:
                seen.add(i)
                out.append(i)
        parts *= 2
    return out


def plant(text: str, pack: TaskPack, *, limit: int = DEFAULT_LIMIT, max_bytes: int = 20_000) -> list[Mutant]:
    """Up to `limit` single-edit mutants of `text`, each passing the RTL rules, in a fixed order."""
    code = mask(text)
    start = _body_start(code, pack.top)
    paren, bracket = _depths(code)
    rules: dict[str, Callable[[str, int, list[int], list[int]], Iterator[_Edit]]] = {
        "drop_reset": _rule_drop_reset(pack),
        "invert_enable": _rule_invert(pack),
        "flip_compare": _rule_flip,
        "off_by_one": _rule_off_by_one,
        "flip_operator": _rule_operator,
        "const_plus_one": _rule_constants,
        "swap_operands": _rule_swap,
    }
    queues: list[list[_Edit]] = []
    for name in RULE_ORDER:
        sites = list(rules[name](code, start, paren, bracket))
        queues.append([sites[i] for i in _spread(len(sites))])

    out: list[Mutant] = []
    seen = {text}
    while len(out) < limit and any(queues):
        for queue in queues:
            if len(out) >= limit:
                break
            while queue:
                edit = queue.pop(0)
                try:
                    rtl = pack.validate_rtl(text[: edit.start] + edit.replacement + text[edit.end :], max_bytes=max_bytes)
                except RtlRejected:
                    continue
                mutated = rtl.text
                if mutated in seen:
                    continue
                seen.add(mutated)
                out.append(
                    Mutant(
                        id=f"m{len(out) + 1:02d}",
                        rule=edit.rule,
                        line=text.count("\n", 0, edit.start) + 1,
                        before=text[edit.start : edit.end].strip()[:60],
                        after=edit.replacement.strip()[:60],
                        text=mutated,
                    )
                )
                break
    return out
