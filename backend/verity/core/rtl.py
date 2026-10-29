"""Rtl: the validated value object every RTL text must become before it reaches a sandbox (ARCHITECTURE 12.3).

Checks done here (pure Python, no tools): size, ASCII, line count, banned constructs, top module present
exactly once, module/endmodule balance. Port names, directions, widths and signedness are checked inside the
sandbox by /verity/bin/verify (Yosys JSON vs task.toml), because that needs a real parser.
"""

import hashlib
import re
from dataclasses import dataclass

from .errors import RtlRejected

ALLOWED_SYSTEM_FUNCS: frozenset[str] = frozenset({"$signed", "$unsigned", "$clog2"})
ALLOWED_DIRECTIVES: frozenset[str] = frozenset(
    {
        "timescale",
        "default_nettype",
        "define",
        "undef",
        "ifdef",
        "ifndef",
        "elsif",
        "else",
        "endif",
        "resetall",
        "celldefine",
        "endcelldefine",
    }
)
# Keyword -> reason. Matched as whole words on comment-stripped source.
BANNED_KEYWORDS: dict[str, str] = {
    "initial": "initial blocks are not allowed (use reset logic)",
    "force": "force is not allowed",
    "release": "release is not allowed",
    "bind": "bind is not allowed",
    "deassign": "deassign is not allowed",
}
DEFAULT_MAX_BYTES = 20_000

_SYSTEM_RE = re.compile(r"(?<![A-Za-z0-9_$\\])\$[A-Za-z_][A-Za-z0-9_$]*")
_DIRECTIVE_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)")
_DPI_RE = re.compile(r"\b(import|export)\s*\"DPI", re.IGNORECASE)
_MODULE_RE = re.compile(r"\b(?:module|macromodule)\s+([A-Za-z_][A-Za-z0-9_$]*)")
_ENDMODULE_RE = re.compile(r"\bendmodule\b")


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments, keep string literals and newlines (so line numbers survive)."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(src[i:j])
            i = j
        elif c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            chunk = src[i : (end + 2 if end >= 0 else n)]
            out.append("\n" * chunk.count("\n"))
            i = end + 2 if end >= 0 else n
        else:
            out.append(c)
            i += 1
    return "".join(out)


def normalize_rtl_text(text: str) -> str:
    """LF line endings, no trailing whitespace on lines, exactly one trailing newline."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def count_code_lines(text: str) -> int:
    """Non-blank lines. This is what maxLines limits."""
    return sum(1 for line in text.split("\n") if line.strip())


def find_problems(text: str, *, top: str, max_lines: int, max_bytes: int = DEFAULT_MAX_BYTES) -> list[str]:
    problems: list[str] = []
    raw_bytes = len(text.encode("utf-8", errors="replace"))
    if raw_bytes > max_bytes:
        problems.append(f"file is {raw_bytes} bytes, the limit is {max_bytes}")
    bad_chars = sorted({ch for ch in text if ord(ch) > 127 or (ord(ch) < 32 and ch not in "\t\n\r")})
    if bad_chars:
        shown = ", ".join(repr(ch) for ch in bad_chars[:5])
        problems.append(f"non-ASCII or control characters are not allowed: {shown}")
    lines = count_code_lines(text)
    if lines > max_lines:
        problems.append(f"{lines} non-blank lines, the limit is {max_lines}")

    code = strip_comments(text)

    for name in sorted(set(_SYSTEM_RE.findall(code)) - ALLOWED_SYSTEM_FUNCS):
        problems.append(f"system task or function {name} is not allowed (only $signed, $unsigned, $clog2)")

    for line in code.split("\n"):
        for directive in _DIRECTIVE_RE.findall(line):
            if directive == "include":
                problems.append("`include is not allowed")
            elif directive == "define" and ('"' in line or "/" in line):
                problems.append("`define with a string or path is not allowed")
            elif directive not in ALLOWED_DIRECTIVES and directive.lower() in {"pragma", "line", "protect"}:
                problems.append(f"`{directive} is not allowed")

    if "``" in code:  # token pasting can build a system task name the check above never sees, e.g. $``display
        problems.append("macro token pasting (``) is not allowed")

    for word, reason in BANNED_KEYWORDS.items():
        if re.search(rf"\b{word}\b", code):
            problems.append(reason)
    if _DPI_RE.search(code):
        problems.append("DPI import/export is not allowed")

    modules = _MODULE_RE.findall(code)
    if not modules:
        problems.append(f"no module found; the top module must be named {top}")
    else:
        if modules.count(top) == 0:
            problems.append(f"top module {top} not found (found: {', '.join(modules[:5])})")
        elif modules.count(top) > 1:
            problems.append(f"module {top} is declared more than once")
        dupes = sorted({m for m in modules if modules.count(m) > 1 and m != top})
        if dupes:
            problems.append(f"modules declared more than once: {', '.join(dupes)}")
        if len(_ENDMODULE_RE.findall(code)) != len(modules):
            problems.append("module and endmodule counts do not match")

    # De-duplicate while keeping order.
    seen: set[str] = set()
    return [p for p in problems if not (p in seen or seen.add(p))]


@dataclass(frozen=True, slots=True)
class Rtl:
    """Validated, normalized Verilog text for one attempt. Construct only via Rtl.validate()."""

    text: str
    top: str
    sha256: str
    lines: int

    @classmethod
    def validate(cls, text: str, *, top: str, max_lines: int, max_bytes: int = DEFAULT_MAX_BYTES) -> "Rtl":
        normalized = normalize_rtl_text(text)
        problems = find_problems(normalized, top=top, max_lines=max_lines, max_bytes=max_bytes)
        if problems:
            raise RtlRejected(problems)
        return cls(
            text=normalized,
            top=top,
            sha256=hashlib.sha256(normalized.encode("ascii")).hexdigest(),
            lines=count_code_lines(normalized),
        )

    @property
    def data(self) -> bytes:
        return self.text.encode("ascii")
