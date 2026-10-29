"""Tolerant extraction of what the roles ask the model for: one fenced Verilog block, or one JSON object.

Pure functions, no I/O. Models are untrusted: nothing here executes or evals model text.
"""

import json
import re
from typing import Any


class ParseError(ValueError):
    """The model answered but the expected block could not be found. The message is safe to show the model."""


_THINK_PAIR_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```[ \t]*(verilog|systemverilog|sv|v)?[ \t]*\r?\n(.*)\Z", re.DOTALL | re.IGNORECASE)
_VERILOG_LANGS = frozenset({"", "verilog", "systemverilog", "sv", "v"})

# Characters models like to emit that the project forbids or that Rtl validation would reject.
_ASCII_FOLD = str.maketrans(
    {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus sign
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00d7": "x",
        "\u2192": "->",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u00a0": " ",
    }
)


# ---------- text hygiene ----------
def strip_think(text: str) -> str:
    """Drop reasoning blocks. Handles a closed pair, a lone closing tag (opening tag was in the chat template)
    and an unterminated opening tag (reasoning was cut off)."""
    text = _THINK_PAIR_RE.sub("", text)
    lowered = text.lower()
    if "</think>" in lowered:
        text = text[lowered.rindex("</think>") + len("</think>") :]
    lowered = text.lower()
    if "<think>" in lowered:
        text = text[: lowered.index("<think>")]
    return text


def ascii_fold(text: str) -> str:
    """Replace the common non-ASCII punctuation (dashes, curly quotes, arrows). Other characters are kept."""
    return text.translate(_ASCII_FOLD)


def clean_line(text: str, limit: int) -> str:
    """One-line, ASCII, truncated text for reason and diagnosis strings that end up in the UI."""
    folded = ascii_fold(text)
    folded = "".join(ch for ch in folded if ch.isascii())
    collapsed = " ".join(folded.split())
    return collapsed[:limit].rstrip()


# ---------- fields ----------
def extract_field(text: str, label: str) -> str | None:
    """Value of a `LABEL: value` line outside code fences (case-insensitive, tolerates markdown emphasis)."""
    body = _FENCE_RE.sub("", strip_think(text))
    pattern = re.compile(rf"^[ \t>*_#-]*{re.escape(label)}[ \t*_]*:[ \t*_]*(.+?)[ \t*_]*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(body)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def prose_before_fence(text: str) -> str | None:
    """First prose sentence outside the code block, used when the model skipped the REASON line."""
    body = strip_think(text)
    cut = body.find("```")
    head = body if cut < 0 else body[:cut]
    head = re.sub(r"^[ \t>*_#-]*(REASON|DIAGNOSIS)[ \t*_]*:", "", head, flags=re.IGNORECASE | re.MULTILINE)
    head = " ".join(head.split())
    if not head:
        return None
    sentence = re.split(r"(?<=[.!?])\s", head, maxsplit=1)[0]
    return sentence or None


# ---------- Verilog ----------
def extract_verilog(text: str, top: str) -> str:
    """The Verilog source from a model answer.

    Accepts ```verilog, ```systemverilog, ```v, ```sv and bare fences. With several blocks, takes the last one that
    declares `module <top>` (a later block is the model's final answer), else the last block. Falls back to an
    unterminated fence, then to bare source between `module` and `endmodule`.
    """
    body = strip_think(text)
    blocks = [code for lang, code in _FENCE_RE.findall(body) if lang.lower() in _VERILOG_LANGS]

    if not blocks:
        opened = _OPEN_FENCE_RE.search(body)
        if opened is not None:
            blocks = [opened.group(2)]

    if not blocks:
        start = re.search(r"^[ \t]*(?:`[A-Za-z_]+[^\n]*\n[ \t]*)*module\b", body, re.MULTILINE)
        end = body.rfind("endmodule")
        if start is not None and end > start.start():
            blocks = [body[start.start() : end + len("endmodule")]]

    if not blocks:
        raise ParseError("no ```verilog fenced block was found in the answer")

    top_re = re.compile(rf"\bmodule\s+{re.escape(top)}\b")
    matching = [b for b in blocks if top_re.search(b)]
    chosen = (matching or blocks)[-1]
    chosen = ascii_fold(chosen).strip("\n").rstrip()

    if not chosen.strip():
        raise ParseError("the verilog block is empty")
    if "endmodule" not in chosen:
        raise ParseError("the verilog block has no endmodule (the answer looks cut off)")
    return chosen


# ---------- JSON ----------
def extract_json_object(text: str) -> dict[str, Any]:
    """First JSON object in the answer: fenced ```json block first, then the first decodable `{...}` anywhere."""
    body = strip_think(text)
    candidates = [code for lang, code in _FENCE_RE.findall(body) if lang.lower() in ("json", "")]
    candidates.append(body)
    decoder = json.JSONDecoder()
    last_error = "no JSON object was found in the answer"
    for candidate in candidates:
        index = candidate.find("{")
        while index >= 0:
            try:
                value, _ = decoder.raw_decode(candidate, index)
            except ValueError as exc:
                last_error = f"invalid JSON: {exc}"
                index = candidate.find("{", index + 1)
                continue
            if isinstance(value, dict):
                return value
            index = candidate.find("{", index + 1)
    raise ParseError(last_error)


# ---------- RTL answers (rtl_gen, repair, optimizer) ----------
REASON_LIMIT = 200
DIAGNOSIS_LIMIT = 300


class RtlAnswer:
    """What an RTL role extracts from one model answer. `text` is not yet validated against the coding rules."""

    __slots__ = ("text", "reason", "diagnosis")

    def __init__(self, text: str, reason: str | None, diagnosis: str | None) -> None:
        self.text = text
        self.reason = reason
        self.diagnosis = diagnosis


def parse_rtl_answer(text: str, top: str, *, want_diagnosis: bool = False) -> RtlAnswer:
    """One fenced Verilog block plus optional `REASON:` and `DIAGNOSIS:` lines. Raises ParseError."""
    rtl = extract_verilog(text, top)
    reason_raw = extract_field(text, "REASON") or prose_before_fence(text)
    reason = clean_line(reason_raw, REASON_LIMIT) if reason_raw else None
    diagnosis: str | None = None
    if want_diagnosis:
        diagnosis_raw = extract_field(text, "DIAGNOSIS") or reason_raw
        diagnosis = clean_line(diagnosis_raw, DIAGNOSIS_LIMIT) if diagnosis_raw else None
    return RtlAnswer(rtl, reason or None, diagnosis or None)
