"""R2/R5 helpers, pure text, no tools and no model: lint tag parsing, guidance, the declaration fixer, failure signatures.

The fixer is deliberately conservative. It only rewrites declarations (wire/reg to logic, a body redeclaration of a
port or signal, stray generate/endgenerate keywords, a declaration inside an always block hoisted to module level).
It never touches an expression or the logic of a block. `normalize_comment_chars` only ever changes comment text.
"""

import re
from dataclasses import dataclass

from .rtl import strip_comments

MAX_GUIDANCE = 4
APPLY_EVERYWHERE = "Apply this fix to every module and signal with the same pattern, not only the one named."

# Tag -> one guidance line. Tags are Verilator tags, or Verity names for untagged messages and structural findings.
GUIDANCE: dict[str, str] = {
    "PROCASSWIRE": (
        "Procedural assignment to a wire: declare that signal as `logic` (never `wire` or `reg`) and drive it only "
        "from an always_ff or always_comb block."
    ),
    "DUPLICATE": (
        "Duplicate declaration: a port declared in the header must not be declared again in the body. "
        "Delete the second declaration and keep one `logic` declaration per name."
    ),
    "GENERATE": (
        "Unexpected generate or endgenerate: do not use generate or genvar. Write the logic with plain always_comb "
        "and always_ff blocks and `for` loops over an int variable."
    ),
    "MULTIDRIVEN": "A signal is driven from more than one block: drive each signal from exactly one always_ff or always_comb block.",
    "BLKANDNBLK": (
        "Blocking and non-blocking assignments to the same signal: use `<=` only in always_ff and `=` only in "
        "always_comb, and never mix them for one signal."
    ),
    "WIDTH": (
        "Width mismatch: make both sides of the assignment the same width with an explicit slice, concatenation or "
        "extension; do not rely on silent truncation."
    ),
    "UNDECLARED": (
        "Undeclared identifier: declare every internal signal as `logic` with its width before use, and check the "
        "spelling against the port list."
    ),
    "LATCH": (
        "Latch inferred: an always_comb output or temporary is not assigned on every path. On the first line of the "
        "block give every signal it assigns a default (for example `y = '0;`), then override it in the branches, and "
        "end every case with a `default`."
    ),
    "NESTING": (
        "Invalid nesting: declare every signal, int and loop temporary at module level, before the first always "
        "block. Never write a `logic`, `int` or `integer` declaration inside an always block."
    ),
    "MULTIMODULE": "The file must contain exactly one module: inline every helper module into the top module.",
}

_TAG_ALIASES = {
    "WIDTHTRUNC": "WIDTH",
    "WIDTHEXPAND": "WIDTH",
    "WIDTHCONCAT": "WIDTH",
    "PROCASSWIRE": "PROCASSWIRE",
    "MULTIDRIVEN": "MULTIDRIVEN",
    "BLKANDNBLK": "BLKANDNBLK",
    "WIDTH": "WIDTH",
}
_TAGGED_RE = re.compile(r"^%(?:Error|Warning)-([A-Z0-9_]+):\s*(.*)$", re.MULTILINE)
_UNTAGGED_RE = re.compile(r"^%Error:\s*(.*)$", re.MULTILINE)
_SIGNAL_RE = re.compile(r"'([A-Za-z_][A-Za-z0-9_$]*)'")
_LOCATION_RE = re.compile(r"^[^\s:]+:\d+(?::\d+)?:\s*")
# Yosys contract errors reach the repair prompt as "- ERROR: ..." bullets, not as %Error lines.
_LATCH_RE = re.compile(r"Latch inferred for signal\s+[`']([^`']+)'")
_NESTING_RE = re.compile(r"Invalid nesting of always blocks")


# Typographic characters a model puts in comments (non-breaking hyphen, en and em dash, minus sign, curly quotes) that
# the RTL rules reject as non-ASCII. Mapped only inside comments.
_COMMENT_MAP: dict[int, str] = {
    **{ord(ch): "-" for ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"},
    **{ord(ch): "'" for ch in "\u2018\u2019\u201a\u201b\u2032"},
    **{ord(ch): '"' for ch in "\u201c\u201d\u201e\u201f\u2033"},
}


def normalize_comment_chars(src: str) -> str:
    """Map Unicode hyphens, dashes and quotes inside // and /* */ comments to ASCII. Code and string literals are never
    touched, and any other non-ASCII character is left for the RTL rules to reject as before."""
    if src.isascii():
        return src
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
        elif c == "/" and nxt in ("/", "*"):
            if nxt == "/":
                end = src.find("\n", i)
                j = n if end < 0 else end
            else:
                end = src.find("*/", i + 2)
                j = n if end < 0 else end + 2
            out.append(src[i:j].translate(_COMMENT_MAP))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


@dataclass(frozen=True, slots=True)
class LintIssue:
    tag: str
    signal: str = ""


def lint_issues(log: str | None) -> list[LintIssue]:
    """Known issues named by a Verilator log, in log order, one per (tag, signal)."""
    if not log:
        return []
    found: list[tuple[int, LintIssue]] = []
    for m in _TAGGED_RE.finditer(log):
        tag = _TAG_ALIASES.get(m.group(1))
        if tag:
            found.append((m.start(), _issue(tag, m.group(2))))
    for m in _UNTAGGED_RE.finditer(log):
        message = _LOCATION_RE.sub("", m.group(1))
        lower = message.lower()
        tag = ""
        if "duplicate declaration" in lower:
            tag = "DUPLICATE"
        elif re.search(r"unexpected\s+(end)?generate\b|unexpected\s+genvar\b", lower):
            tag = "GENERATE"
        elif "can't find definition" in lower or "not declared" in lower or "undeclared" in lower:
            tag = "UNDECLARED"
        if tag:
            found.append((m.start(), _issue(tag, message)))
    for m in _LATCH_RE.finditer(log):
        found.append((m.start(), LintIssue("LATCH", _leaf(m.group(1)))))
    for m in _NESTING_RE.finditer(log):
        found.append((m.start(), LintIssue("NESTING")))
    seen: set[LintIssue] = set()
    out: list[LintIssue] = []
    for _, issue in sorted(found, key=lambda pair: pair[0]):
        if issue not in seen:
            seen.add(issue)
            out.append(issue)
    return out


def _issue(tag: str, message: str) -> LintIssue:
    m = _SIGNAL_RE.search(_LOCATION_RE.sub("", message))
    name = m.group(1) if m else ""
    return LintIssue(tag, _leaf(name) if tag == "LATCH" else name)


def _leaf(hier: str) -> str:
    """Last component of a hierarchical name such as `matmul4.unnamedblk1.row_i` (Yosys prefixes a backslash)."""
    return re.split(r"[.\\]", hier)[-1]


def structural_tags(text: str) -> list[str]:
    """Cheap structural check on the RTL text: more than one module, generate present."""
    code = strip_comments(text)
    tags: list[str] = []
    if len(re.findall(r"\b(?:module|macromodule)\b", code)) > 1:
        tags.append("MULTIMODULE")
    if re.search(r"\b(?:generate|endgenerate|genvar)\b", code):
        tags.append("GENERATE")
    return tags


def guidance_for(lint_log: str | None, rtl_text: str) -> list[str]:
    """Up to 4 guidance lines for the tags the log and the structure show (fewer if fewer matched)."""
    tags: list[str] = []
    for tag in [i.tag for i in lint_issues(lint_log)] + structural_tags(rtl_text):
        if tag not in tags:
            tags.append(tag)
    return [GUIDANCE[t] for t in tags[:MAX_GUIDANCE]]


def guidance_block(lint_log: str | None, rtl_text: str) -> str:
    """Prompt text for the repair role, or "" when no known tag matched."""
    lines = guidance_for(lint_log, rtl_text)
    if not lines:
        return ""
    return "Known fixes for these errors:\n" + "\n".join(f"- {line}" for line in lines) + "\n" + APPLY_EVERYWHERE


# ======================================================================
# failure signature (R5)
# ======================================================================
def failure_signature(lint: str, lint_log: str | None, failing_case: str | None) -> str:
    """Stable id of why an attempt failed: lint tag and signal, else the first failing test name."""
    if lint == "fail":
        issues = lint_issues(lint_log)
        if issues:
            return f"lint:{issues[0].tag}:{issues[0].signal}"
        log = (lint_log or "").strip()
        bullet = re.search(r"^- (.+)$", log, re.MULTILINE)
        if bullet:
            return "rules:" + re.sub(r"\d+", "N", bullet.group(1))[:80]
        first = _UNTAGGED_RE.search(log)
        message = _LOCATION_RE.sub("", first.group(1)) if first else log.split("\n")[0]
        return "lint:other:" + re.sub(r"\d+", "N", message)[:80]
    return f"test:{failing_case or 'unknown'}"


# ======================================================================
# deterministic declaration fixer (R2)
# ======================================================================
@dataclass(frozen=True, slots=True)
class FixResult:
    text: str
    changes: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


_WIRE_INIT_RE = re.compile(
    r"^(\s*)wire\s+(signed\s+)?(\[[^\]]*\]\s*)?([A-Za-z_]\w*)\s*=\s*([^;]+);[ \t]*$", re.MULTILINE
)
_KEYWORD_RE = re.compile(r'(//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*")|\b(wire|reg)\b', re.DOTALL)
_PORT_RE = re.compile(r"\b(?:input|output|inout)\s+(logic\s+)?(signed\s+)?(\[[^\]]*\])?\s*([A-Za-z_]\w*)")
_BODY_DECL_RE = re.compile(r"^\s*logic\s+(signed\s+)?(\[[^\]]*\]\s*)?([A-Za-z_]\w*)\s*;\s*(?://.*)?$")
_GEN_LINE_RE = re.compile(r"^\s*(?:generate|endgenerate)\s*(?://.*)?$")


# Lint tags the declaration fixer can act on. Any other lint failure goes straight to the repair role.
FIXABLE_TAGS = frozenset({"PROCASSWIRE", "DUPLICATE", "GENERATE", "NESTING"})


def fixer_applies(lint_log: str | None) -> bool:
    return any(i.tag in FIXABLE_TAGS for i in lint_issues(lint_log))


def fix_declarations(text: str) -> FixResult:
    """Text-level declaration fixes. Idempotent. Returns the input unchanged when nothing applies."""
    changes: list[str] = []
    out = text

    # 1. `wire x = expr;` is a continuous assign; keep that meaning as `logic x; assign x = expr;`.
    def _split_init(m: re.Match[str]) -> str:
        indent, signed, rng, name, expr = m.groups()
        return f"{indent}logic {signed or ''}{rng or ''}{name};\n{indent}assign {name} = {expr.strip()};"

    out, n = _WIRE_INIT_RE.subn(_split_init, out)
    if n:
        changes.append(f"split {n} wire declaration{'s' if n != 1 else ''} with an initializer into logic plus assign")

    # 2. wire / reg become logic (comments and strings are left alone).
    count = 0

    def _to_logic(m: re.Match[str]) -> str:
        nonlocal count
        if m.group(1) is not None:
            return m.group(1)
        count += 1
        return "logic"

    out = _KEYWORD_RE.sub(_to_logic, out)
    if count:
        changes.append(f"changed {count} wire/reg declaration{'s' if count != 1 else ''} to logic")

    # 3. A body declaration that repeats a port or an earlier signal of the same type is dropped.
    declared: dict[str, tuple[str, bool]] = {}
    for m in _PORT_RE.finditer(strip_comments(out)):
        if m.group(1):  # ANSI port already declared `logic`
            declared[m.group(4)] = (_norm_range(m.group(3)), bool(m.group(2)))
    kept: list[str] = []
    for line in out.split("\n"):
        m = _BODY_DECL_RE.match(line)
        if m:
            name, key = m.group(3), (_norm_range(m.group(2)), bool(m.group(1)))
            if declared.get(name) == key:
                changes.append(f"removed the duplicate declaration of {name}")
                continue
            declared.setdefault(name, key)
        kept.append(line)
    out = "\n".join(kept)

    # 4. Bare generate / endgenerate keywords are optional in SystemVerilog; unwrap them unless genvar is used.
    if not re.search(r"\bgenvar\b", strip_comments(out)):
        lines = out.split("\n")
        stripped = [ln for ln in lines if not _GEN_LINE_RE.match(ln)]
        if len(stripped) != len(lines):
            changes.append(f"removed {len(lines) - len(stripped)} generate/endgenerate keyword line(s)")
            out = "\n".join(stripped)

    # 5. A declaration inside an always block goes to module level (Yosys: "Invalid nesting of always blocks").
    out, hoisted = _hoist_block_declarations(out)
    changes.extend(hoisted)

    return FixResult(out if changes else text, changes)


def _norm_range(rng: str | None) -> str:
    return re.sub(r"\s+", "", rng or "")


_ALWAYS_RE = re.compile(r"\balways(?:_comb|_ff|_latch)?\b")
_BEGIN_RE = re.compile(r"\bbegin\b")
_END_RE = re.compile(r"\bend\b")
_LOCAL_DECL_RE = re.compile(
    r"^(\s*)(logic|int|integer|bit|byte)\s+(signed\s+)?(\[[^\]]*\]\s*)?([A-Za-z_]\w*)\s*(?:=\s*([^;]+))?;\s*(?://.*)?$"
)


def _hoist_block_declarations(text: str) -> tuple[str, list[str]]:
    """Move `logic x;` / `int x = e;` found inside an always block to module level, just above that block.

    A declaration with an initializer stays as an assignment at the old position (`x = e;`). A name already declared
    in the file with a different type or width is left alone.
    """
    module_decls: dict[str, str] = {}
    for m in _PORT_RE.finditer(strip_comments(text)):
        module_decls[m.group(4)] = "port"
    out: list[str] = []
    changes: list[str] = []
    depth = 0  # begin/end nesting inside the current always block
    in_always = False
    block_start = 0  # index in `out` of the line holding the `always` keyword
    hoisted: list[str] = []
    for line in text.split("\n"):
        code = line.split("//", 1)[0]
        m = _LOCAL_DECL_RE.match(line)
        if not in_always and _ALWAYS_RE.search(code):
            in_always, depth, block_start, hoisted = True, 0, len(out), []
        elif not in_always and m:
            module_decls.setdefault(m.group(5), _decl_text(m))
        elif in_always and depth > 0 and m:
            name, decl = m.group(5), _decl_text(m)
            if module_decls.get(name, decl) == decl:
                if name not in module_decls:
                    module_decls[name] = decl
                    hoisted.append(f"    {decl};")
                changes.append(f"moved the declaration of {name} out of an always block to module level")
                if m.group(6) is not None:
                    out.append(f"{m.group(1)}{name} = {m.group(6).strip()};")
                continue
        out.append(line)
        if in_always:
            depth += len(_BEGIN_RE.findall(code)) - len(_END_RE.findall(code))
            if depth <= 0 and (_BEGIN_RE.search(code) or _END_RE.search(code) or ";" in code):
                in_always = False
                out[block_start:block_start] = hoisted
    return "\n".join(out), changes


def _decl_text(m: re.Match[str]) -> str:
    parts = (m.group(2), (m.group(3) or "").strip(), _norm_range(m.group(4)), m.group(5))
    return " ".join(p for p in parts if p)
