"""FakeProvider: zero network, deterministic, so the whole pipeline (and the web app) runs at $0.

Answer source, in order:
  1. A hand-curated cassette: backend/evals/cassettes/<task_id>/<role>[.<strategy>][.<n>].txt (the raw model text).
  2. A canned answer built from the task pack. This is the ONLY place reference.v is read at run time (it never
     reaches a real prompt or a sandbox). Behavior is chosen so every path of the loop is exercised:
       planner    a plausible plan for the pack's family; replans apply the steering carried in tags
       rtl_gen    reference.v with one deliberate small bug, so verify fails and the FIX path runs
       repair     reference.v (distinct text per failing input, so it is never a duplicate)
       optimizer  SIMPLIFY and OPTIMIZE: reference.v with a variant comment (valid, distinct hash);
                  EXPLORE: a buggy variant, so the child repair path runs once per EXPLORE child

Variants differ only textually, so the fake children have the same area and clock period as v1. Record
cassettes of real model answers to see evolution with real PPA differences.

Tags read from LLMRequest.tags (set by the roles): task_id, strategy, attempt, priority, steering (JSON),
source_sha (repair), parent_sha (optimizer), call_seq (set by the gateway), pack_root (the run's rendered pack,
so knob runs get their own reference; tags are never put in a prompt).
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..config import AGENT_ROOT, Settings
from ..core.budget import estimate_tokens
from ..core.errors import LLMError, VerityError
from ..core.llm_types import LLMRequest, LLMResponse
from ..core.pack import TaskPack

DEFAULT_CASSETTE_DIR = AGENT_ROOT / "evals" / "cassettes"

_NO_CODE_LINE = re.compile(r"\s*(localparam|parameter|input|output|genvar|integer)\b")
_BUG_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<=\S) \+ (?=\S)"), " - "),
    (re.compile(r"(?<=\S) & (?=\S)"), " | "),
    (re.compile(r"(?<=\S) \| (?=\S)"), " & "),
)


# A request to build something, even when worded as a question ("can you build a 16 bit adder?"), is not a question.
_FAKE_BUILD_REQUEST = re.compile(
    r"^\s*(please\s+)?((can|could|would|will) you (please )?)?"
    r"(build|make|design|create|generate|implement|give me|i need|i want|i'd like|i would like|we need)\b",
    re.IGNORECASE,
)
_FAKE_QUESTION_START = re.compile(
    r"^\s*(what|what's|whats|how|why|when|where|who|which|can|could|does|do|is|are|will|would|should|tell me|explain|"
    r"hi|hello|hey|thanks|thank you)\b",
    re.IGNORECASE,
)


def _fake_is_question(text: str) -> bool:
    """A trailing question mark, or a leading question word, with no request to build something."""
    if _FAKE_BUILD_REQUEST.match(text):
        return False
    return text.rstrip().endswith("?") or bool(_FAKE_QUESTION_START.match(text))


def _fake_parse_json(request: str) -> str:
    """The fact-check answer shape (class, label, family, params, reason, closest), from keywords only."""
    low = request.lower()
    bits = re.search(r"(\d+)[- ]?bit", low)
    width = {"width": int(bits.group(1))} if bits else {}
    answer: dict[str, Any]
    if _fake_is_question(request) and not re.search(r"\b(which|choose)\b.*\b(cpus?|processors?|gpus?)\b|\b(cpus?|processors?|gpus?)\b.*\b(build|available|offer)\b", low):
        answer = {"class": "question", "label": "", "reason": "A question about chips.", "closest": None}
    elif re.search(r"\b(gpus?|graphics (?:processors?|processing units?))\b", low) or "6090" in low:
        answer = {
            "class": "too_big",
            "label": "edge camera gpu" if "camera" in low else "gpu",
            "reason": "That is a full GPU: thousands of cores, memories and caches. Verity builds small single-module blocks.",
            "closest": {"family": "mac4", "params": {"lanes": 8, "width": 8, "cycles": 32}},
        }
    elif re.search(r"\b(cpus?|processors?|microcontrollers?|rv32\w*|risc[ -]?v)\b", low):
        answer = {
            "class": "cpu_choice",
            "label": "cpu",
            "reason": "Choose a CPU on the CPU card, then review the plan before confirming.",
            "closest": None,
        }
    elif "counter" in low and any(w in low for w in ("counts to", "resets", "modulo", " mod ")):
        top = re.search(r"to (\d+)", low)
        limit = int(top.group(1)) if top else 100
        answer = {
            "class": "near_family",
            "label": "modulo counter",
            "reason": f"A counter that resets at {limit} is close to the binary counter, which wraps at a power of two.",
            "closest": {"family": "cnt8", "params": {"width": max(4, min(16, limit.bit_length()))}},
        }
    elif "counter" in low:
        answer = {"class": "in_family", "label": "counter", "family": "cnt8", "params": width}
    elif "adder" in low:
        answer = {"class": "in_family", "label": f"{bits.group(1)}-bit adder" if bits else "adder", "family": "add8", "params": width}
    elif "pattern" in low or "detector" in low:
        pattern = "".join(c for c in low if c in "01")
        answer = {
            "class": "in_family",
            "label": "sequence detector",
            "family": "fsm_seq",
            "params": {"pattern": pattern} if pattern else {},
        }
    elif "multipl" in low or "accumulat" in low or "mac" in low.split():
        answer = {"class": "in_family", "label": "multiply accumulate", "family": "mac4", "params": {}}
    elif "machine" in low or "chip" in low or "circuit" in low:
        answer = {
            "class": "unsure",
            "label": "machine controller timer",
            "reason": "A controller for a machine is a small control block. A timer built from a counter is the closest.",
            "closest": {"family": "cnt8", "params": {}},
        }
    else:
        answer = {
            "class": "not_chip",
            "label": "",
            "reason": "That is not an electronic circuit, so there is nothing for Verity to build.",
            "closest": None,
        }
    return json.dumps(answer)


_SOURCE_LINE = re.compile(r"^S(\d+) \(")


def _fake_research_json(user: str) -> str:
    """A summary that cites the sources it was given and names allowed techniques found in their text."""
    from ..research.techniques import TECHNIQUES

    sources: dict[int, str] = {}
    current: int | None = None
    for line in user.splitlines():
        found = _SOURCE_LINE.match(line)
        if found:
            current = int(found.group(1))
            sources[current] = ""
        elif current is not None and (line.startswith("title: ") or line.startswith("snippet: ")):
            sources[current] += " " + line.split(": ", 1)[1].lower()
    allowed_line = next((ln for ln in user.splitlines() if ln.startswith("Technique ids you may use: ")), "")
    allowed = {t.strip() for t in allowed_line.split(": ", 1)[-1].split(",")}
    ids = sorted(sources)
    sentences = []
    if ids:
        sentences.append({"text": "These sources describe how this kind of block is built.", "sourceIds": ids[:2]})
    techniques = []
    for family in TECHNIQUES.values():
        for tech in family:
            if tech.id not in allowed:
                continue
            cited = [n for n in ids if any(k in sources[n] for k in tech.keywords)]
            if cited:
                techniques.append({"id": tech.id, "sourceIds": cited[:2]})
    return json.dumps({"sentences": sentences, "techniques": techniques, "doneBefore": bool(ids)})


_FAKE_NEW_CHIP = re.compile(
    r"^\s*(please\s+)?(design|build|make|create|give me|i want|i need|i'd like)\s+(me\s+)?(a|an|another|some)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
# Chip nouns by family; the empty key holds chips no family builds. A request for the run's own family is not a new chip.
_FAKE_CHIP_NOUNS: dict[str, re.Pattern[str]] = {
    "add8": re.compile(r"\badders?\b", re.IGNORECASE),
    "cnt8": re.compile(r"\b(counters?|timers?)\b", re.IGNORECASE),
    "mac4": re.compile(r"\b(multipl\w*|mac)\b", re.IGNORECASE),
    "fsm_seq": re.compile(r"\b(fsm|state machine|sequence detector|pattern)\b", re.IGNORECASE),
    "": re.compile(r"\b(uart|spi|i2c|fifo|alu|cpu|gpu|npu|tpu|soc|processor|cache|memory|pwm|filter)\b", re.IGNORECASE),
}


def _fake_wants_new_chip(user: str, system: str) -> bool:
    """A build request for a chip noun other than the run's own family (the real router judges this itself)."""
    found = _FAKE_NEW_CHIP.match(user)
    if found is None:
        return False
    family = re.search(r"\(chip family (\S+?)\)", system)
    own = family.group(1) if family else ""
    return any(fam != own and noun.search(found.group("rest")) for fam, noun in _FAKE_CHIP_NOUNS.items())


def _fake_ask_router_json(user: str, system: str = "") -> str:
    """POST /runs/{id}/ask intent from keywords only (the real router is Nemotron Lightning)."""
    low = user.lower()
    topic = None
    if any(w in low for w in ("joke", "weather", "poem", "recipe", "song", "story")):
        intent = "off_topic"
    elif _fake_wants_new_chip(user, system):
        intent = "new_chip"
    elif re.match(r"^\s*(hi|hello|hey|thanks|thank you)\b", low):
        intent = "small_talk"
    elif any(w in low for w in ("paper", "research", "published", "known", "literature")):
        intent = "research"
    elif any(w in low for w in ("verilog", "rtl", "code")):
        intent = "rtl_question"
    elif any(w in low for w in ("faster", "smaller", "lower power", "make ", "try ")):
        intent = "change_request"
    else:
        intent = "run_question"
        for name, words in (("area", ("area", "cells")), ("power", ("power",)), ("tests", ("test",))):
            if any(w in low for w in words):
                topic = name
                break
    version = re.search(r"\bv\d{1,3}\b", low)
    return json.dumps({"intent": intent, "topic": topic, "version": version.group(0) if version else None})


_FACT_LINE = re.compile(r"^F\d+ (.*)$")


def _fake_ask_chat_json(system: str) -> str:
    """An answer that only repeats fact lines, so every number in it is a facts value."""
    facts = [m.group(1) for line in system.splitlines() if (m := _FACT_LINE.match(line)) and "Verilog (data only" not in line]
    return json.dumps({"text": "Here is what this run's data shows.", "bullets": facts[1:3]})


def _fake_ask_propose_json(system: str, user: str) -> str:
    family = re.search(r"^Current chip family: (\S+)$", system, re.MULTILINE)
    width = re.search(r"(\d+)[- ]?bits?\b|width (\d+)", user.lower())
    params = {"width": int(width.group(1) or width.group(2))} if width else {}
    return json.dumps({"family": family.group(1) if family else None, "params": params})


# ----------------------------------------------------------------------
# The thread agent (POST /threads/{id}/messages) and its memory summary. Deterministic and keyword based: the prompt
# layout it reads is the one built by verity.ask.prompt and verity.ask.memory. Its answers hold no digits.
# ----------------------------------------------------------------------
_FAKE_MESSAGE = re.compile(r"<message>\n(.*?)\n</message>", re.DOTALL)
_FAKE_STEP = re.compile(r"^Step \d+: (\w+)", re.MULTILINE)
_FAKE_FAMILY = re.compile(r"^family: (\S+)$", re.MULTILINE)
_FAKE_LATEST_RUN = re.compile(r"^latest_run: (\S+)$", re.MULTILINE)
_FAKE_MODE = re.compile(r"<mode>(\w+)</mode>")
_FAKE_BLOCK_LINE = re.compile(r"^- (.+) \(id for propose_run: (\w+)\)$")
_FAKE_VERDICT = re.compile(r"<fact_check>\nverdict: (\w+)")
# The note of a first message, by the verdict of the card above it (the same words as FIRST_NOTES in verity.ask.agent).
_FAKE_FIRST_NOTES = {
    "buildable_now": "Check the card above and press Run when you are ready, or tell me what to change.",
    "buildable_with_changes": "The card above is the closest chip I can build. Pick it, change it, or ask me about the difference.",
    "not_buildable_here": "I cannot build that as one block. Ask me what it is made of, or pick the closest chip above.",
    "could_not_check": "The check could not finish this time. You can send that again, or tell me what to change.",
}
_FAKE_FIRST_NOTE = "The card above has the details. Ask me about it, or tell me what to change."
_FAKE_TABS = (("verilog", "verilog"), ("architecture", "architecture"), ("sign-off", "signoff"), ("signoff", "signoff"), ("versions", "versions"), ("result", "result"))


def _fake_knob_names(system: str, family: str) -> set[str]:
    """The setting names listed under `- <title> (id for propose_run: <family>)` in the <buildable> block."""
    names: set[str] = set()
    inside = False
    for line in system.splitlines():
        if line.startswith("- "):
            inside = line.endswith(f"(id for propose_run: {family})")
        elif inside and line.startswith("    ") and ":" in line:
            names.add(line.strip().split(":", 1)[0])
        elif inside and line.startswith("</buildable>"):
            break
    return names


def _fake_named_family(low: str, system: str) -> str | None:
    """The family a message names (adder, counter, sequence detector or pattern, MAC or multiply-accumulate), when the
    buildable list has it."""
    return next((fam for fam, noun in _FAKE_CHIP_NOUNS.items() if fam and f"(id for propose_run: {fam})" in system and noun.search(low)), None)


def _fake_agent_json(system: str, user: str) -> str:
    """One JSON action of the thread agent, from keywords and the facts block only."""
    found = _FAKE_MESSAGE.search(user)
    low = (found.group(1) if found else "").lower()
    used = _FAKE_STEP.findall(user)
    mode = (_FAKE_MODE.search(system) or [None, "discussion_no_run"])[1]
    family = (_FAKE_FAMILY.search(system) or [None, None])[1]
    run = (_FAKE_LATEST_RUN.search(system) or [None, None])[1]

    def tool(name: str, args: dict[str, Any]) -> str:
        return json.dumps({"action": "tool", "tool": name, "args": args})

    def answer(text: str, **extra: Any) -> str:
        return json.dumps({"action": "answer", "text": text, "bullets": [], **extra})

    if "<answer_only>" in user:
        return answer("I can help with chips and chip design. Tell me what you want to build, or ask about a block.")
    if any(w in low for w in ("what can you build", "what can you make", "what components", "which components", "what blocks", "which blocks", "components")):
        if "list_blocks" not in used:
            return tool("list_blocks", {})
        chips = [m.group(1) for line in user.splitlines() if (m := _FAKE_BLOCK_LINE.match(line.strip()))]
        return answer("These are the blocks I can build today. A small custom block can often be drafted too.", chips=chips[:5])
    if any(w in low for w in ("walk me through", "walkthrough", "step by step", "how was this built")):
        if run is None:
            return answer("There is no finished run in this thread yet, so there is nothing to walk through.")
        if "walkthrough" not in used:
            return tool("walkthrough", {})
        return answer("Here is how Verity built it, step by step.")
    if run is not None and any(w in low for w in ("open ", "show me the ")):
        tab = next((value for word, value in _FAKE_TABS if word in low), None)
        if tab is not None:
            if "open_tab" not in used:
                return tool("open_tab", {"tab": tab})
            return answer("I opened it for you.")
    if run is not None and any(w in low for w in ("timing", "critical path", "why was", "kept", "cost", "tests")):
        topic = "timing" if ("timing" in low or "critical path" in low) else "cost" if "cost" in low else "tests" if "test" in low else "selection"
        if "run_facts" not in used:
            return tool("run_facts", {"topic": topic})
        result = re.search(r'<tool_result tool="run_facts">\n(.*?)\n</tool_result>', user, re.DOTALL)
        facts = [ln.strip() for ln in (result.group(1) if result else "").splitlines() if ln.strip()]
        return answer("Here is what this run's data shows.", bullets=facts[:2])
    if mode != "first_message" and any(w in low for w in ("custom block", "draft a block", "draft_custom_block")):
        if "draft_custom_block" not in used:
            return tool("draft_custom_block", {"description": (found.group(1) if found else low)[:200]})
        return answer("I drafted it and checked it against its own worked examples. Press Approve to lock it in.")
    named = _fake_named_family(low, system)
    target = named or family  # a family the message names wins over the thread's own
    # a family the message names is a request for it, unless the message is a question
    wants = any(w in low for w in ("faster", "smaller", "lower power", "less power", "speed", "area", "bit")) or (
        named is not None and not low.rstrip().endswith("?")
    )
    if target is not None and mode != "first_message" and wants:
        if "propose_run" not in used:
            width = re.search(r"(\d+)[- ]?bit", low)
            knobs = {"width": int(width.group(1))} if width and "width" in _fake_knob_names(system, target) else {}
            args: dict[str, Any] = {"family": target, "knobs": knobs}
            for words, priority in ((("faster", "speed"), "speed"), (("smaller", "area"), "area"), (("power",), "power")):
                if any(w in low for w in words):
                    args["priority"] = priority
                    break
            return tool("propose_run", args)
        return answer("Here is a new run for you to confirm.")
    if mode == "first_message":
        return answer(_FAKE_FIRST_NOTES.get((_FAKE_VERDICT.search(system) or [None, ""])[1], _FAKE_FIRST_NOTE))
    return answer("I can help with chips and chip design. Tell me what you want to build, or ask about a block.")


def _fake_memory_json(user: str) -> str:
    """A summary made of the first words the user said in the turns being folded (and the summary so far)."""
    previous = re.search(r"<previous_summary>\n(.*?)\n</previous_summary>", user, re.DOTALL)
    said = [line[len("User: "):] for line in user.splitlines() if line.startswith("User: ")]
    words = " ".join(([previous.group(1)] if previous and previous.group(1).strip() else []) + said).split()
    return json.dumps({"summary": "Earlier in this thread the user asked about: " + " ".join(words[:24])})


def _fake_custom_draft_json(_user: str) -> str:
    """A deterministic stand-in draft (CE3): always a correct, self-check-passing 8-bit popcount, whatever the
    description said. Real language understanding is Nemotron/GLM's job, not the fake provider's; this exercises
    the full draft -> validate -> self-check pipeline for $0."""
    return json.dumps(
        {
            "id": "popcount8",
            "title": "8-bit popcount",
            "summary": "Counts the set bits of an 8-bit input, combinational",
            "specText": "Output count is the number of 1 bits in input x, an 8-bit unsigned value. Combinational, no clock.",
            "top": "popcount8",
            "elementType": "uint8",
            "latencyCycles": 0,
            "ports": [
                {"name": "x", "dir": "in", "width": 8, "signed": False},
                {"name": "count", "dir": "out", "width": 4, "signed": False},
            ],
            "clockName": "",
            "resetName": "",
            "goldenPy": "def golden_step(state, inputs, rst):\n    x = inputs.get('x', 0) & 0xFF\n    return {}, {'count': bin(x).count('1')}\n",
            "cases": [
                {"name": "zero", "steps": [{"inputs": {"x": 0}, "rst": False, "expected": {"count": 0}}]},
                {"name": "all_ones", "steps": [{"inputs": {"x": 255}, "rst": False, "expected": {"count": 8}}]},
                {"name": "single_bit", "steps": [{"inputs": {"x": 16}, "rst": False, "expected": {"count": 1}}]},
                {"name": "two_bits", "steps": [{"inputs": {"x": 17}, "rst": False, "expected": {"count": 2}}]},
            ],
        }
    )


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        settings: Settings,
        *,
        delay_s: float = 0.2,
        inject_bug: bool = True,
        cassette_dir: Path | None = None,
    ) -> None:
        self._tasks_dir = settings.tasks_dir
        self._delay_s = delay_s
        self._inject_bug = inject_bug
        self._cassette_dir = cassette_dir or DEFAULT_CASSETTE_DIR
        self._packs: dict[str, TaskPack] = {}

    async def aclose(self) -> None:
        return None

    # ---------- entry ----------
    async def complete(self, req: LLMRequest) -> LLMResponse:
        tags = req.tags
        if tags.get("purpose") == "parse":
            return await self._parse_answer(req)
        if tags.get("purpose") == "research":
            return await self._short_answer(req, _fake_research_json(req.user), "fake-research")
        if tags.get("purpose") == "ask_router":
            return await self._short_answer(req, _fake_ask_router_json(req.user, req.system), "fake-ask-router")
        if tags.get("purpose") == "ask_chat":
            return await self._short_answer(req, _fake_ask_chat_json(req.system), "fake-ask-chat")
        if tags.get("purpose") == "ask_propose":
            return await self._short_answer(req, _fake_ask_propose_json(req.system, req.user), "fake-ask-propose")
        if tags.get("purpose") == "agent":
            return await self._short_answer(req, _fake_agent_json(req.system, req.user), "fake-agent")
        if tags.get("purpose") == "memory":
            return await self._short_answer(req, _fake_memory_json(req.user), "fake-memory")
        if tags.get("purpose") == "custom_draft":
            return await self._short_answer(req, _fake_custom_draft_json(req.user), "fake-custom-draft")
        task_id = tags.get("task_id", "")
        if not task_id:
            raise LLMError("fake provider needs the task_id tag", retryable=False)
        pack = self._pack(task_id, tags.get("pack_root", ""))
        strategy = tags.get("strategy", "")
        attempt = tags.get("attempt", "")

        text = self._cassette(task_id, req.role, strategy, attempt)
        if text is None:
            text = self._canned(req, pack)

        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        digest = hashlib.sha256(
            "|".join([task_id, req.role, strategy, attempt, tags.get("call_seq", "")]).encode("utf-8")
        ).hexdigest()
        return LLMResponse(
            text=text,
            input_tokens=estimate_tokens(req.system + req.user),
            output_tokens=estimate_tokens(text),
            model=req.model,
            request_id=f"fake-{digest[:12]}",
            latency_ms=int(self._delay_s * 1000),
            finish_reason="stop",
        )

    # ---------- POST /parse ----------
    async def _parse_answer(self, req: LLMRequest) -> LLMResponse:
        """Deterministic stand-in for the parse call. The request text is the user message."""
        return await self._short_answer(req, _fake_parse_json(req.user), "fake-parse")

    async def _short_answer(self, req: LLMRequest, text: str, request_id: str) -> LLMResponse:
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        return LLMResponse(
            text=text,
            input_tokens=estimate_tokens(req.system + req.user),
            output_tokens=estimate_tokens(text),
            model=req.model,
            request_id=request_id,
            latency_ms=int(self._delay_s * 1000),
            finish_reason="stop",
        )

    # ---------- sources ----------
    def _pack(self, task_id: str, pack_root: str = "") -> TaskPack:
        """The run's own rendered pack when the roles pass its root (knob runs), else tasks/<id> (default knobs)."""
        if not re.fullmatch(r"[a-z0-9_]+", task_id):
            raise LLMError(f"bad task id {task_id!r}", retryable=False)
        root = Path(pack_root) if pack_root else self._tasks_dir / task_id
        key = str(root)
        pack = self._packs.get(key)
        if pack is None:
            try:
                pack = TaskPack.load(root)
            except (VerityError, OSError) as exc:
                raise LLMError(f"fake provider cannot load task pack {task_id}: {exc}", retryable=False) from exc
            self._packs[key] = pack
        return pack

    def _cassette(self, task_id: str, role: str, strategy: str, attempt: str) -> str | None:
        folder = self._cassette_dir / task_id
        names: list[str] = []
        if strategy and attempt:
            names.append(f"{role}.{strategy}.{attempt}.txt")
        if strategy:
            names.append(f"{role}.{strategy}.txt")
        if attempt:
            names.append(f"{role}.{attempt}.txt")
        names.append(f"{role}.txt")
        for name in names:
            path = folder / name
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return None

    # ---------- canned answers ----------
    def _canned(self, req: LLMRequest, pack: TaskPack) -> str:
        if req.role == "planner":
            return json.dumps(_plan_json(pack, req.tags), indent=1)

        reference = pack.reference_rtl()
        if reference is None:
            raise LLMError(
                f"fake provider has no reference.v for task {pack.id}; add a cassette under {self._cassette_dir / pack.id}",
                retryable=False,
            )
        reference = reference.replace("\r\n", "\n").strip("\n")
        tags = req.tags
        call_seq = tags.get("call_seq", "0")

        if req.role == "rtl_gen":
            body = _inject_bug(reference) if self._inject_bug else reference
            return _answer(
                "Straightforward datapath that follows the plan block by block.",
                body,
            )
        if req.role == "repair":
            source = tags.get("source_sha", "x")[:8]
            fixed = f"// verity-fake: repaired copy of {source}\n{reference}"
            return _answer(
                "Restored the intended behavior of the datapath.",
                fixed,
                diagnosis="The arithmetic in the datapath did not match the expected values, so the operator was corrected.",
            )
        # optimizer
        strategy = tags.get("strategy", "OPTIMIZE")
        parent = tags.get("parent_sha", "x")[:8]
        header = f"// verity-fake: {strategy} variant {call_seq} of {parent}\n"
        if strategy == "EXPLORE" and self._inject_bug:
            return _answer(
                "Tried a different structure for the datapath.",
                header + _inject_bug(reference),
            )
        reasons = {
            "SIMPLIFY": "Removed redundant structure while keeping the same behavior.",
            "OPTIMIZE": "Tightened the datapath for the chosen priority while keeping the same latency.",
            "EXPLORE": "Tried a different structure for the datapath.",
        }
        return _answer(reasons.get(strategy, "Variant of the parent design."), header + reference)


# ======================================================================
# helpers
# ======================================================================
def _answer(reason: str, rtl: str, *, diagnosis: str | None = None) -> str:
    parts = []
    if diagnosis is not None:
        parts.append(f"DIAGNOSIS: {diagnosis}")
    parts.append(f"REASON: {reason}")
    parts.append("")
    parts.append("```verilog")
    parts.append(rtl.rstrip("\n"))
    parts.append("```")
    return "\n".join(parts) + "\n"


def _inject_bug(text: str) -> str:
    """Flip the first suitable operator after the port list. Returns the text unchanged if none is found."""
    lines = text.split("\n")
    start = next((i + 1 for i, line in enumerate(lines) if ");" in line.split("//")[0]), 0)
    for pattern, replacement in _BUG_RULES:
        for i in range(start, len(lines)):
            line = lines[i]
            code = line.split("//")[0]
            if not code.strip() or _NO_CODE_LINE.match(code):
                continue
            if pattern.search(code):
                lines[i] = pattern.sub(replacement, line, count=1)
                return "\n".join(lines)
    return text


# ---------- plans ----------
def _block(
    block_id: str,
    name: str,
    reason: str,
    pin_options: list[str],
    variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "name": name,
        "reason": reason,
        "pinOptions": pin_options,
        "pins": [],
        "variants": variants or [],
    }


def _variant(variant_id: str, label: str, options: list[str], value: str) -> dict[str, Any]:
    return {"id": variant_id, "label": label, "options": options, "value": value}


def _blocks_for(pack: TaskPack) -> list[dict[str, Any]]:
    if pack.family == "mac":
        return [
            _block("in", "Input registers", "Capture the input pairs once per cycle.", ["Minimize registers"]),
            _block(
                "mult",
                "Multiplier array (4 lanes)",
                "One multiplier per lane.",
                ["Minimize area", "Share one multiplier"],
                [_variant("pipeline", "Pipeline", ["none", "2-stage"], "none")],
            ),
            _block("add", "Adder tree", "Add the lane products into one sum.", ["Minimize area", "Minimize registers"]),
            _block(
                "acc",
                "Accumulator and control",
                "Keep the running sum, count the cycles, raise done.",
                ["Minimize registers"],
                [_variant("width", "Accumulator width", ["auto", "exact"], "auto")],
            ),
        ]
    if pack.family == "alu":
        return [
            _block("decode", "Operation decode", "Turn the opcode into datapath controls.", ["Minimize area"]),
            _block(
                "arith",
                "Add and subtract unit",
                "One shared adder handles add, subtract and compare.",
                ["Minimize area", "Shorten the carry path"],
            ),
            _block("logic", "Logic and shift unit", "AND, OR, XOR and the two shifts.", ["Minimize area"]),
            _block("flags", "Result register and flags", "Register the result and set zero and carry.", ["Minimize registers"]),
        ]
    if pack.family == "matmul":
        return [
            _block("in", "Row input registers", "Capture one row of each matrix per cycle.", ["Minimize registers"]),
            _block(
                "mac",
                "Multiply-accumulate lanes",
                "Compute the dot products from the captured rows.",
                ["Minimize area", "Share one multiplier"],
                [_variant("pipeline", "Pipeline", ["none", "2-stage"], "none")],
            ),
            _block("ctl", "Loop control", "Count rows and columns, raise done at the end.", ["Minimize registers"]),
            _block("out", "Result registers", "Hold the results until reset.", ["Minimize registers"]),
        ]
    return [
        _block("in", "Input stage", "Capture and align the inputs.", ["Minimize registers"]),
        _block("core", "Core logic", "Compute the result described in the spec.", ["Minimize area"]),
        _block("out", "Output stage", "Register the outputs and signal completion.", ["Minimize registers"]),
    ]


def _port_text(pack: TaskPack) -> str:
    items = []
    for p in pack.ports:
        shape = f"{p.count} x {p.width} bits" if p.count > 1 else f"{p.width} bits"
        items.append(f"{p.name} ({p.dir}, {shape})")
    return ", ".join(items)


def _plan_json(pack: TaskPack, tags: Any) -> dict[str, Any]:
    blocks = _blocks_for(pack)
    steering: list[dict[str, Any]] = []
    raw = tags.get("steering", "")
    if raw:
        try:
            decoded = json.loads(raw)
            steering = [s for s in decoded if isinstance(s, dict)] if isinstance(decoded, list) else []
        except ValueError:
            steering = []
    for item in steering:
        block = next((b for b in blocks if b["id"] == item.get("blockId")), None)
        if block is None:
            continue
        if item.get("kind") == "pin" and item.get("pin") and item["pin"] not in block["pins"]:
            block["pins"].append(item["pin"])
        elif item.get("kind") == "toggle":
            variant = next((v for v in block["variants"] if v["id"] == item.get("variantId")), None)
            if variant is not None and item.get("value") in variant["options"]:
                variant["value"] = item["value"]
    priority = tags.get("priority", pack.default_priority)
    return {
        "summary": f"{pack.title}: {len(blocks)} blocks, fixed latency of {pack.latency_cycles} cycles, tuned for {priority}.",
        "blocks": blocks,
        "interfaces": _port_text(pack),
        "dataWidths": f"element type {pack.element_type}; widths as in the port list",
        "latencyCycles": pack.latency_cycles,
        "verification": f"Checked against the locked golden model on {pack.test_count} cases.",
        "risks": ["Accumulator or result width could be too small for the worst case."],
        "estimatedLines": min(pack.max_lines, 90),
    }
