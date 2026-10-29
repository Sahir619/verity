"""AgentService: POST /threads/{id}/messages (backend ARCHITECTURE 17.4). After the first chip request every message is a
conversation with an agent that acts like a senior RTL and ASIC engineer, picks its own tools and shows the steps.

    message()   load the thread -> [the fact check, on a thread that has not had one] -> hold the worst case ->
                turn() -> save the facts and the turn -> fold the oldest turns into the summary (in batches of 4)
    turn()      the loop: one JSON action per model call on the `chat` route (GLM 5.3). At most 4 tool steps, then the
                model must answer (a 5th call asks for an answer only); at most 5 calls in all. Code runs a tool only
                through the table in agent_tools.py. After `agent.turn_deadline_s` (90 s) it stops calling the model and
                code answers with what the steps found; every call also tells the provider the time left (deadline_s),
                so a fallback model is never given longer than that and none is tried with under 5 s left. The answer
                then goes through four checks of code:
                  - strip_verilog (fenced blocks and every sentence with Verilog), after a bare pack id is turned into
                    the block's plain name (no size) and with no em dash or en dash left, and with every bullet or chip
                    that is a piece of JSON dropped (clean_answer),
                  - every number must be in the facts block, a tool result, the user's own message or the fact check
                    of this message (the summary and the earlier turns never count; 1.862 and 1.8620 are the same
                    number); one retry names the bad numbers and asks for each needed number copied exactly as the
                    facts write it, then a code-written answer replaces the reply,
                  - once a run has finished in the thread, a sentence that calls a design proven, proved, formally verified
                    or equivalent to another needs an EQY pass in the run facts or a tool result (the run's own EQY
                    records, never the model's words; a pass against v1 backs any such sentence, a pass against the
                    human-written design backs a sentence about the human design). Without a pass, one retry names the
                    true EQY status, and if the retry still claims it those sentences are cut and the rest of the answer
                    stays (a denial such as "not a proof" passes),
                  - a model never starts a run or a CPU job: propose_run makes a chip card and the user presses Run;
                    propose_cpu_job, propose_cpu_retry and propose_cpu_upgrade make a CPU card (checked first by the
                    job manager's own rules) and the user presses Confirm.
    Text inside tool results, earlier turns and the summary is data: code acts on none of it. A step line is shown once:
    a repeated call, a second chip card and a tool the mode does not offer have none, and identical neighbouring lines
    collapse into one (shown_steps).

Money: message() holds the worst case first (5 chat calls at their output cap with the full context, one research, and the
summary calls of the fold this message can start) on the daily and the session budget, so a refusal happens before the fact
check spends anything. A message runs at most one new research (agent_tools.py), which is why the hold covers one. Every
call reports its real cost. The fake provider holds and spends nothing.
"""

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import TypeAdapter

from ..config import RoleName, Settings
from ..contracts import (
    AgentAnswer,
    AgentCpuProposal,
    AgentProposal,
    AgentRefused,
    AgentReply,
    OpenTab,
    ParseInRange,
    ParseReply,
    ThreadListEntry,
    ThreadMessageReply,
    ThreadsResponse,
    ThreadTurn,
    ThreadView,
    ToolStep,
    to_wire,
)
from ..core.budget import price_usd, spend_owner
from ..core.captions import plain_dashes
from ..core.errors import LLMError, NotFound
from ..core.family import Knob, ParamsError, validate_params
from ..core.llm_types import LLMRequest
from ..core.pack import TaskPack
from ..core.protocols import LLMProvider, Store, ThreadStore, ToolRunner
from ..core.records import ACTIVE_STATUSES, THREAD_USER_MAX, THREAD_VERITY_MAX, RunRecord, ThreadData, ThreadTurnIn
from ..research.service import ResearchService
from . import memory, prompt, reply
from .agent_tools import CpuChecks, ToolBox, ToolOutcome
from .tools import RunCard, load_card, model_name

log = logging.getLogger("verity.ask")

AGENT_TAG = "agent"
MAX_STEPS = 4  # tool steps in one message; after them the model must answer
MAX_CALLS = MAX_STEPS + 1  # model calls in one message, a number-check retry included
MAX_TOOL_TEXT = 3_000  # characters of one tool result shown to the model
CHECK_ALLOWANCE_CHARS = 4_000  # the fact check's block, for the hold taken before the check has run
MAX_CHIPS = 5
MAX_CHIP_CHARS = 40
FIRST_NOTE_SENTENCES = 2
FIRST_NOTE_BULLETS = 3
RUNS_SHOWN = 5  # runs of the thread described in the facts block
SESSION_SHOWN = 5  # other CPU jobs and latest runs listed in <session>
NOTED_JOBS = 3  # jobs in <cpu_job> whose failed core runs get their failure notes
# Every CPU pack id (the seven blocks, the cores and each job's derived packs) starts with this; no chip pack does. A CPU
# job's runs belong to the job, which the chat shows as its CPU card and board, so they are never runs of the chat.
CPU_PACK_PREFIX = "rv32i_"

BASIS = "From this thread's facts and the steps above."
BASIS_CODE = "Written by code from this thread's facts and the steps above. No model wording was used."
# The code note of a first message when the agent cannot write one: it matches the verdict on the card above it.
FIRST_NOTES = {
    "cpu_choice": "Choose a kind on the CPU card, then review the blocks and goal before confirming.",
    "buildable_now": "Check the card above and press Run when you are ready, or tell me what to change.",
    "buildable_with_changes": "The card above is the closest chip I can build. Pick it, change it, or ask me about the difference.",
    "not_buildable_here": "I cannot build that as one block. Ask me what it is made of, or pick the closest chip above.",
    "could_not_check": "The check could not finish this time. You can send that again, or tell me what to change.",
}
FALLBACK_NOTE = "The card above has the details. Ask me about it, or tell me what to change."  # a verdict not listed above
FALLBACK_REFUSED = "I could not answer that just now. Try again in a moment."

Hold = Callable[[float, str], AbstractAsyncContextManager[None]]  # (worst case in dollars, session id)
Parse = Callable[[str, str], Awaitable[ParseReply]]  # (text, session id): the fact check of POST /parse

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_REPLY_ADAPTER: TypeAdapter[Any] = TypeAdapter(AgentReply)  # reads a stored wire reply back (GET /threads/{id})
_CHECK_ADAPTER: TypeAdapter[Any] = TypeAdapter(ParseReply)


def _iso_z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# ======================================================================
# reading and cleaning the model's action
# ======================================================================
@dataclass(slots=True)
class Action:
    kind: Literal["tool", "answer"]
    tool: object = None
    args: object = None
    text: str = ""
    bullets: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def read_action(raw: str) -> Action | None:
    """The one JSON object of a model call, or None when it cannot be read. Prose with no object is an answer."""
    body = raw.strip()
    start = body.find("{")
    if start < 0:
        return Action(kind="answer", text=body) if body else None
    try:
        data = json.JSONDecoder().raw_decode(body, start)[0]
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("action")
    if kind == "tool":
        return Action(kind="tool", tool=data.get("tool"), args=data.get("args"))
    if kind == "answer" or (kind is None and isinstance(data.get("text"), str)):
        text = data.get("text")
        if not isinstance(text, str):
            return None
        return Action(kind="answer", text=text, bullets=_strings(data.get("bullets")), chips=_strings(data.get("chips")))
    return None


def first_sentences(text: str, count: int) -> str:
    return " ".join(_SENTENCE.split(text.strip())[:count])


def clean_answer(action: Action, mode: str, titles: Mapping[str, str]) -> tuple[str, list[str], list[str]]:
    """(text, bullets, chips) after a pack id becomes its plain name, strip_verilog, the dash rule, the length caps and
    the removal of any bullet or chip that is a piece of JSON; a first-message note is cut to two sentences and three
    bullets. The number check runs on what this returns."""
    text, bullets = reply._clean(reply.plain_ids(action.text, titles), [reply.plain_ids(b, titles) for b in action.bullets])
    chips: list[str] = []
    for chip in action.chips:
        chip = plain_dashes(reply.strip_verilog(reply.plain_ids(chip, titles)))[:MAX_CHIP_CHARS].strip()
        if chip and not reply.is_json_fragment(chip) and chip not in chips:
            chips.append(chip)
    if mode == "first_message":
        text, bullets = first_sentences(text, FIRST_NOTE_SENTENCES), bullets[:FIRST_NOTE_BULLETS]
    return text, bullets, chips[:MAX_CHIPS]


def allowed_numbers(sources: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for source in sources:
        out.update(reply.numbers_in(source))
    return out


def shown_steps(steps: Sequence[ToolOutcome]) -> list[ToolStep]:
    """The step lines the user sees: only the steps code shows (a repeat, a tool that was not offered and a name that does
    not exist have none), with identical neighbouring labels collapsed into one."""
    out: list[ToolStep] = []
    for s in steps:
        if s.shown and not (out and out[-1].label == s.label):
            out.append(ToolStep(tool=s.tool, label=s.label, ok=s.ok))  # type: ignore[arg-type]
    return out


def verity_text(answer: AgentReply) -> str:
    """What the thread keeps of a reply: its words, with the bullets after them."""
    if isinstance(answer, AgentAnswer) and answer.bullets:
        return answer.text + " " + " ".join(f"- {b}" for b in answer.bullets)
    return answer.text


# ======================================================================
# one turn's prepared context
# ======================================================================
@dataclass(slots=True)
class Prepared:
    mode: str
    system: str  # persona, tools, action format, mode rules and the <verity_facts> block
    facts_text: str  # the <verity_facts> block: one source of the numbers an answer may use
    base_user: str  # <earlier_summary> and <earlier_turns>
    message: str  # the <message> block, always last
    focus_version: str | None
    verdict: str | None = None  # the verdict of this message's fact check (first_message mode only)
    eqy: str = ""  # the latest run's equivalence proof fact (walkthrough.eqy_facts), for the note of a proof retry
    passes: frozenset[str] = frozenset()  # what EQY proved in that run: "v1" and/or "human"
    can_build_cpu: bool = False  # settings.can_build of this user (signed in), decided by the route
    cpu_admin: bool = False  # settings.is_admin of this user: only an admin may name another account's CPU job

    def user(self, steps: Sequence[ToolOutcome], tail: str = "") -> str:
        parts = [self.base_user, steps_block(steps)]
        if tail:
            parts.append(tail)
        parts.append(self.message)
        return "\n".join(parts)


def steps_block(steps: Sequence[ToolOutcome]) -> str:
    if not steps:
        return "<steps_so_far>\nnone\n</steps_so_far>"
    lines = ["<steps_so_far>"]
    for n, step in enumerate(steps, start=1):
        lines.append(f"Step {n}: {step.tool}, {'ok' if step.ok else 'refused'}")
        lines.append(f'<tool_result tool="{step.tool}">\n{memory.data(step.text[:MAX_TOOL_TEXT])}\n</tool_result>')
    lines.append("</steps_so_far>")
    return "\n".join(lines)


@dataclass(slots=True)
class TurnResult:
    reply: AgentReply
    facts: dict[str, Any]  # the facts record after the turn (the proposal is written by code)
    tool_steps: list[dict[str, Any]]  # for the thread's Verity row


class AgentService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        threads: ThreadStore,
        provider: LLMProvider,
        packs: Mapping[str, TaskPack],
        families: Mapping[str, list[Knob]],
        research: ResearchService | None,
        *,
        tools: ToolRunner,
        hold: Hold,
        record: Callable[[float, str], Awaitable[None]],
        parse: Parse,
        register_custom_pack: Callable[[TaskPack], None],
        cpu_jobs: Callable[[str, str], Awaitable[list[Any]]] | None = None,
        cpu_job: Callable[[str, str], Awaitable[Any]] | None = None,
        my_cpu_jobs: Callable[[str, set[str], int], Awaitable[list[Any]]] | None = None,
        link_cpu_job: Callable[[str, str, str], Awaitable[bool]] | None = None,
        cpu_checks: CpuChecks | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._threads = threads
        self._provider = provider
        self._packs = dict(packs)
        self._families = dict(families)
        self._titles = {fid: memory.plain_name(fid, packs[fid].title) for fid in families if fid in packs}  # what the user reads instead of a pack id
        self._research = research
        self._tools = tools  # CE3: draft_custom_block's self-check (check_golden), no RTL involved
        self._hold = hold  # reserves a worst case on the daily and session budgets; raises AdmissionDenied
        self._record = record  # adds a real cost to the daily and session spend (the fake provider spends nothing)
        self._parse = parse
        self._register_custom_pack = register_custom_pack  # CE3: locks a drafted block in at Approve, no restart needed
        self._cpu_jobs = cpu_jobs  # (session id, thread id) -> the CPU jobs started from that chat, for <cpu_job>
        self._cpu_job = cpu_job  # (job id, session id) -> one CPU job's view (the one on screen), for <cpu_job>
        self._my_cpu_jobs = my_cpu_jobs  # (session id, ids to leave out, at most) -> this owner's CPU jobs, newest first, for <session>
        self._link_cpu_job = link_cpu_job  # (job id, session id, thread id) -> point an owner's job at this chat
        self._cpu_checks = cpu_checks  # the CPU job manager's rules for a CPU card, and a failed core's notes
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ======================================================================
    # one message
    # ======================================================================
    async def message(
        self,
        thread_id: str,
        *,
        session_id: str,
        text: str,
        run_id: str | None = None,
        focus_version: str | None = None,
        cpu_job_id: str | None = None,
        can_build_cpu: bool = False,
        cpu_admin: bool = False,
    ) -> ThreadMessageReply:
        """`run_id` and `cpu_job_id`, when given, were already checked by the route (this session's run; a CPU job it may read).
        `can_build_cpu`: the route's settings.can_build for this user; the facts say it and the CPU card tools obey it.
        `cpu_admin`: its settings.is_admin, which alone lets a CPU card name another account's job."""
        owner = spend_owner(session_id)
        async with self._lock(owner, thread_id):
            thread = await self._threads.load(owner, thread_id, since_summary=True)
            facts = dict(thread.facts) if thread is not None and thread.facts else memory.new_facts()
            if run_id and run_id not in facts.get("run_ids", []):  # the web may send the run id with every message
                facts = await self._with_run(facts, run_id)  # a run means a fact check already happened
            prep = await self._prepare(thread, facts, text, session_id, None, focus_version, cpu_job_id, can_build_cpu, cpu_admin)
            waiting = len(thread.turns) // 2 if thread is not None else 0  # turns the summary does not cover yet
            folded = memory.fold_size(waiting + 1)  # the turn this message adds can start a fold: hold for the turns it takes
            async with self._hold(self._worst_usd(prep, with_check=not facts.get("checked"), fold_turns=folded), session_id):
                shown: ParseReply | None = None
                check: ParseReply | None = None
                if not facts.get("checked"):
                    parsed = await self._parse(text, session_id)
                    if parsed.intent_class == "question" or parsed.verdict == "not_a_chip":
                        # No check card for a question or for something that is not a chip: the agent answers (it
                        # stays on chips and says what Verity builds) and the thread stays unchecked.
                        pass
                    else:
                        shown = check = parsed
                        facts = memory.with_check(facts, parsed)
                        await self._save_facts(owner, thread_id, facts)  # the check was paid for: keep it whatever follows
                        prep = await self._prepare(thread, facts, text, session_id, parsed, focus_version, cpu_job_id, can_build_cpu, cpu_admin)
                result = await self.turn(prep, text, session_id=session_id, facts=facts)
                await self._save_facts(owner, thread_id, result.facts)
                await self._threads.append_turns(
                    owner,
                    thread_id,
                    [
                        ThreadTurnIn(role="user", text=text),
                        ThreadTurnIn(
                            role="verity",
                            text=verity_text(result.reply),
                            steps=result.tool_steps,
                            # the wire reply, so another device can draw the thread again (the store drops it over 32 KB)
                            reply={
                                "check": to_wire(shown) if shown is not None else None,
                                "reply": to_wire(result.reply),
                            },
                        ),
                    ],
                )
                await self._relink_cpu_job(owner, session_id, thread_id, cpu_job_id)
                await self._fold(owner, thread_id, session_id)
                return ThreadMessageReply(thread_id=thread_id, check=shown, reply=result.reply)

    async def attach_run(self, thread_id: str, session_id: str, run: RunRecord) -> None:
        """POST /runs with a threadId: add the run to this session's thread. Another owner's thread, or one that does
        not exist, is left alone."""
        if run.preset_id.startswith(CPU_PACK_PREFIX):
            return
        owner = spend_owner(session_id)
        async with self._lock(owner, thread_id):
            thread = await self._threads.load(owner, thread_id)
            if thread is None:
                return
            facts = self._facts_with_run(dict(thread.facts) if thread.facts else memory.new_facts(), run)
            await self._threads.update_facts(owner, thread_id, facts)

    async def list_threads(self, session_id: str, limit: int = 50) -> ThreadsResponse:
        rows = await self._threads.list_threads(spend_owner(session_id), max(1, min(limit, 50)))
        return ThreadsResponse(
            threads=[ThreadListEntry(id=r.id, title=r.title, updated_at=_iso_z(r.updated_at), run_ids=r.run_ids) for r in rows]
        )

    async def read_thread(self, session_id: str, thread_id: str) -> ThreadView:
        thread = await self._threads.load(spend_owner(session_id), thread_id)
        if thread is None:
            raise NotFound(thread_id)
        turns: list[ThreadTurn] = []
        for row in thread.turns:
            steps: list[ToolStep] | None = None
            if row.role == "verity":
                steps = []
                for raw in row.steps:
                    try:
                        steps.append(ToolStep.model_validate(raw))
                    except ValueError:
                        continue
            turns.append(
                ThreadTurn(
                    seq=row.seq, role=row.role, text=row.text, tool_steps=steps, created_at=_iso_z(row.created_at),
                    **(self._stored_reply(row.reply) if row.role == "verity" else {}),
                )
            )
        run_ids = thread.facts.get("run_ids", [])
        shown: list[str] = []
        for r in run_ids if isinstance(run_ids, list) else []:
            if not isinstance(r, str):
                continue
            run = await self._store.get_run(r)  # a CPU job's run linked before attach_run skipped them is left out too
            if run is None or not run.preset_id.startswith(CPU_PACK_PREFIX):
                shown.append(r)
        return ThreadView(thread_id=thread.id, run_ids=shown, turns=turns)

    @staticmethod
    def _stored_reply(stored: dict[str, Any] | None) -> dict[str, Any]:
        """The reply and the check kept with a Verity row, ready to pass to ThreadTurn. A part that no longer fits the
        wire model (an old shape) is left out; the text and the steps still draw the turn."""
        out: dict[str, Any] = {}
        if not isinstance(stored, dict):
            return out
        for key, adapter in (("reply", _REPLY_ADAPTER), ("check", _CHECK_ADAPTER)):
            value = stored.get(key)
            if isinstance(value, dict):
                try:
                    out[key] = adapter.validate_python(value)
                except ValueError:
                    continue
        return out

    # ======================================================================
    # the loop
    # ======================================================================
    async def turn(self, prep: Prepared, text: str, *, session_id: str, facts: Mapping[str, Any]) -> TurnResult:
        toolbox = ToolBox(
            settings=self._settings,
            store=self._store,
            packs=self._packs,
            families=self._families,
            research=self._research,
            session_id=session_id,
            facts=facts,
            focus_version=prep.focus_version,
            mode=prep.mode,
            message=text,
            provider=self._provider,
            tools=self._tools,
            record=self._record,
            can_build_cpu=prep.can_build_cpu,
            cpu=self._cpu_checks,
            cpu_admin=prep.cpu_admin,
        )
        deadline = time.monotonic() + self._settings.agent.turn_deadline_s
        steps: list[ToolOutcome] = []
        note = ""
        retried = False
        calls = 0
        served: str | None = None  # the model that answered the latest call (a fallback chain can change it)
        answered: AgentReply | None = None
        while calls < MAX_CALLS and answered is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.info("agent: the turn deadline passed after %d call(s)", calls)
                break  # no more model calls: the code answer below, from the steps so far
            answer_only = len(steps) >= MAX_STEPS or bool(note)
            tail = note or (prompt.FORCED_ANSWER if answer_only else "")
            calls += 1
            try:
                action, served = await self._call(prep, steps, tail, calls, session_id, remaining)
            except LLMError:
                log.warning("agent: the model call failed", exc_info=True)
                break
            if action is None:
                log.info("agent: the model answer could not be read")
                break
            if action.kind == "tool":
                if answer_only:
                    break  # an answer was required: the code answer below
                outcome = await self._run_tool(toolbox, action, deadline - time.monotonic())
                if outcome is None:
                    log.info("agent: the turn deadline passed while a tool ran")
                    break
                steps.append(outcome)
                continue
            body, bullets, chips = clean_answer(action, prep.mode, self._titles)
            if not body and not bullets:
                log.info("agent: the answer was empty after the Verilog strip")
                break
            allowed = allowed_numbers([prep.facts_text, text, *(s.text for s in steps)])
            bad = reply.bad_numbers([body, *bullets, *chips], allowed)
            passes = prep.passes.union(*(s.passes for s in steps if s.ok))
            claims = reply.proof_claims([body, *bullets], passes) if prep.mode == "discussion_after_run" else []  # no run, no EQY fact to contradict
            if not bad and (not claims or retried):
                if claims:  # the retry still calls a design proven with no EQY pass: cut those sentences, keep the rest
                    log.info("agent: the answer still claims a proof that no EQY pass backs; %d sentence(s) cut", len(claims))
                    body, bullets = reply.without_proof_claims(body, bullets, passes)
                    if not body and not bullets:
                        break  # nothing is left: the code answer below
                answered = self._compose(prep, body, bullets, chips, steps, templated=False, served=served)
                break
            if bad:
                log.info("agent: the answer used numbers that are not facts: %s", bad)
            if claims:
                log.info("agent: the answer claims a proof and no EQY pass is in the facts: %s", claims)
            if retried:
                break
            retried = True
            note = "".join([prompt.retry_note(bad) if bad else "", prompt.proof_retry_note(reply.proof_note(prep.eqy)) if claims else ""])
        if answered is None:
            answered = self._fallback(prep, steps)
        proposal = _last(steps, "proposal")
        new_facts = memory.with_proposal(facts, proposal.proposal) if proposal is not None and proposal.proposal is not None else dict(facts)
        return TurnResult(
            reply=answered,
            facts=new_facts,
            tool_steps=[{"tool": s.tool, "label": s.label, "ok": s.ok} for s in shown_steps(steps)],
        )

    async def _run_tool(self, toolbox: ToolBox, action: Action, remaining: float) -> ToolOutcome | None:
        """The step, or None when the turn deadline passes first. A tool that is still running is not cancelled: it goes on
        to its own end (a research records its spend there), and the code answer is written without it."""
        if remaining <= 0:
            return None  # no new tool starts after the deadline
        task = asyncio.ensure_future(toolbox.run(action.tool, action.args))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            return None

    async def _call(
        self, prep: Prepared, steps: Sequence[ToolOutcome], tail: str, n: int, session_id: str, remaining: float
    ) -> tuple[Action | None, str]:
        """(the model's action or None, the id of the model that served the call)."""
        model = self._settings.model_for("chat")
        request = LLMRequest(
            role="chat",
            model=model,
            system=prep.system,
            user=prep.user(steps, tail),
            max_output_tokens=self._settings.max_output_for("chat"),
            temperature=self._settings.temperature_for("chat"),
            response_format="json",
            timeout_s=min(float(self._settings.llm_timeout_for("chat")), remaining),  # a call never outlives the turn deadline
            deadline_s=remaining,  # nor does a fallback attempt: the provider caps it at the time left (and skips it under 5 s)
            thinking=self._settings.thinking_for("chat"),
            reasoning_effort=self._settings.reasoning_effort_for("chat"),
            tags={"purpose": AGENT_TAG, "call": str(n)},
        )
        response = await self._provider.complete(request)
        served = response.model or model
        await self._cost(price_usd(self._settings.price_for(served), response.input_tokens, response.output_tokens), session_id)
        return read_action(response.text), served

    async def _cost(self, usd: float, session_id: str) -> None:
        try:
            await self._record(usd, session_id)
        except Exception:
            log.exception("could not record the cost of an agent call")

    # ======================================================================
    # the reply
    # ======================================================================
    def _basis(self, served: str | None = None) -> str:
        sample = getattr(self._provider, "name", "") == "fake"
        worded = "Sample wording (no model was called)" if sample else f"Worded by {model_name(served or self._settings.model_for('chat'))}"
        return f"{BASIS} {worded}; every number checked against the facts and the tool results."

    def _compose(
        self,
        prep: Prepared,
        text: str,
        bullets: list[str],
        chips: list[str],
        steps: Sequence[ToolOutcome],
        *,
        templated: bool,
        open_tab: OpenTab | None = None,
        served: str | None = None,
    ) -> AgentReply:
        tool_steps = shown_steps(steps)
        if not text and bullets:
            text, bullets = bullets[0], bullets[1:]
        basis = BASIS_CODE if templated else self._basis(served)
        proposal = _last(steps, "proposal")
        if proposal is not None and proposal.proposal is not None:
            said = " ".join([text, *(n for n in proposal.notes if n not in text)]).strip()  # code says what it clamped
            return AgentProposal(text=said, proposal=proposal.proposal, tool_steps=tool_steps)
        cpu = _last(steps, "cpu_action")
        if cpu is not None and cpu.cpu_action is not None:
            return AgentCpuProposal(text=text, action=cpu.cpu_action, tool_steps=tool_steps)
        walk = _last(steps, "walk")
        tab = _last(steps, "open_tab")
        found = _last(steps, "research")
        return AgentAnswer(
            text=text,
            bullets=[] if walk is not None else bullets,
            basis=basis,
            templated=templated,
            steps=walk.walk if walk is not None else None,
            tool_steps=tool_steps,
            chips=chips or None,
            open_tab=tab.open_tab if tab is not None else open_tab,
            research=found.research if found is not None else None,
        )

    def _fallback(self, prep: Prepared, steps: Sequence[ToolOutcome]) -> AgentReply:
        """The code-written reply: the plain sentence of the last tool that worked, or a note that fits the verdict of the
        card above it. Every one of them goes through strip_verilog."""
        last = next((s for s in reversed(steps) if s.ok and s.fallback), None)
        if last is not None:
            text = reply.strip_verilog(last.fallback)[: reply.MAX_TEXT].strip()
            if text:
                return self._compose(prep, text, [], [], steps, templated=True, open_tab=last.fallback_tab)
        if prep.mode == "first_message":
            note = reply.strip_verilog(FIRST_NOTES.get(prep.verdict or "", FALLBACK_NOTE))
            return self._compose(prep, note, [], [], steps, templated=True)
        return AgentRefused(text=FALLBACK_REFUSED, tool_steps=shown_steps(steps))

    # ======================================================================
    # context
    # ======================================================================
    async def _prepare(
        self,
        thread: ThreadData | None,
        facts: Mapping[str, Any],
        text: str,
        session_id: str,
        check: ParseReply | None,
        focus_version: str | None,
        cpu_job_id: str | None = None,
        can_build_cpu: bool = False,
        cpu_admin: bool = False,
    ) -> Prepared:
        run_lines, latest_run, card = await self._thread_runs(facts, session_id)
        if check is not None:
            mode = "first_message"
        else:
            mode = "discussion_after_run" if card is not None else "discussion_no_run"
        focus = focus_version if card is not None and focus_version in card.labels else None
        check_text = None
        if check is not None:
            family = check.family if isinstance(check, ParseInRange) else (check.closest.family if check.closest else None)
            pack = self._packs.get(family) if family else None
            check_text = memory.check_block(check, memory.plain_name(family, pack.title) if pack is not None and family else None)
        run = reply.build_facts(card, [], include_rtl=False) if card is not None else None
        run_facts = run.lines if run is not None else []
        cpu_jobs, on_screen, session, notes = await self._cpu_context(thread, session_id, cpu_job_id)
        facts_text = memory.facts_block(
            mode,
            memory.buildable_lines(self._packs, self._families),
            memory.thread_block(facts, run_lines, latest_run, focus),
            check_text,
            run_facts,
            memory.cpu_jobs_block(cpu_jobs, on_screen, notes),
            session,
            can_build_cpu=can_build_cpu,
        )
        turns = thread.turns[-2 * memory.SHOWN_TURNS :] if thread is not None else []
        summary = thread.summary if thread is not None else ""
        return Prepared(
            mode=mode,
            system=prompt.system_prompt(mode, facts_text),
            facts_text=facts_text,
            base_user=memory.summary_block(summary) + "\n" + memory.turns_block(turns),
            message=f"<message>\n{memory.data(text)}\n</message>",
            focus_version=focus,
            verdict=check.verdict if check is not None else None,
            eqy=run.eqy if run is not None else "",
            passes=run.passes if run is not None else frozenset(),
            can_build_cpu=can_build_cpu,
            cpu_admin=cpu_admin,
        )

    async def _cpu_context(
        self, thread: ThreadData | None, session_id: str, cpu_job_id: str | None
    ) -> tuple[list[Any], str | None, str | None, dict[str, list[list[str]]]]:
        """(the jobs of <cpu_job>: the one on screen first, then this chat's; the on-screen job's id; the <session> block;
        job id -> the failure notes of each failed core run, for the first NOTED_JOBS of those jobs whose core failed).
        Every lookup is best effort: one that fails is logged and its part is left out, and the message goes on."""
        owner = spend_owner(session_id)
        chat_jobs: list[Any] = []
        if thread is not None and self._cpu_jobs is not None:
            try:
                chat_jobs = await self._cpu_jobs(session_id, thread.id)
            except Exception:
                log.warning("agent: could not read this chat's CPU jobs", exc_info=True)
        screen = None
        if cpu_job_id and self._cpu_job is not None:
            try:
                screen = await self._cpu_job(cpu_job_id, session_id)
            except Exception:
                log.warning("agent: could not read the CPU job on screen", exc_info=True)
        jobs = ([screen] if screen is not None else []) + [j for j in chat_jobs if screen is None or j.id != screen.id]
        others: list[Any] = []
        if self._my_cpu_jobs is not None:
            try:
                shown = {j.id for j in jobs}
                others = [j for j in await self._my_cpu_jobs(session_id, shown, SESSION_SHOWN) if j.id not in shown][:SESSION_SHOWN]
            except Exception:
                log.warning("agent: could not list this owner's CPU jobs", exc_info=True)
        runs: list[Any] = []
        try:
            runs = await self._threads.list_user_runs(owner, SESSION_SHOWN)
        except Exception:
            log.warning("agent: could not list this owner's runs", exc_info=True)
        session = memory.session_block(others, runs, thread.id if thread is not None else None)
        notes: dict[str, list[list[str]]] = {}
        if self._cpu_checks is not None:  # only jobs already read above: the one on screen (checked by the route) and this chat's
            for job in [j for j in jobs if j.core.status == "failed"][:NOTED_JOBS]:
                try:
                    notes[job.id] = await self._cpu_checks.core_notes(job.id)
                except Exception:
                    log.warning("agent: could not read the failed core of a CPU job", exc_info=True)
        return jobs, (screen.id if screen is not None else None), session, notes

    async def _relink_cpu_job(self, owner: str, session_id: str, thread_id: str, cpu_job_id: str | None) -> None:
        """A message answered about the CPU job on screen: if the owner's job points at no chat, or at one that is gone,
        point it at this chat. Another owner's job and a job whose chat still exists are left alone. Best effort."""
        if not cpu_job_id or self._cpu_job is None or self._link_cpu_job is None:
            return
        try:
            view = await self._cpu_job(cpu_job_id, session_id)
            if view is None or view.thread_id == thread_id:
                return
            # since_summary: only whether the chat exists matters, not every turn it ever had
            if view.thread_id is not None and await self._threads.load(owner, view.thread_id, since_summary=True) is not None:
                return
            await self._link_cpu_job(cpu_job_id, session_id, thread_id)  # refuses a job of another owner
        except Exception:
            log.warning("agent: could not link the CPU job to this chat", exc_info=True)

    async def _thread_runs(self, facts: Mapping[str, Any], session_id: str) -> tuple[list[str], str | None, RunCard | None]:
        """(a line per run of this thread, the id of the latest finished run, that run's card). A run id of the facts
        record whose row is gone or belongs to another session is skipped."""
        ids = [i for i in facts.get("run_ids", []) if isinstance(i, str)][-RUNS_SHOWN:]
        runs: list[RunRecord] = []
        for run_id in ids:
            run = await self._store.get_run(run_id)
            if run is not None and run.session_id == session_id:
                runs.append(run)
        finished = next((r for r in reversed(runs) if r.status not in ACTIVE_STATUSES), None)
        card = await load_card(self._store, finished) if finished is not None else None
        lines = []
        for run in runs:
            kept = card.winner.label if card is not None and finished is not None and run.id == finished.id and card.winner else None
            lines.append(memory.run_line(run, self._full_params(run), kept))
        return lines, (finished.id if finished is not None else None), card

    def _full_params(self, run: RunRecord) -> dict[str, Any]:
        knobs = self._families.get(run.preset_id)
        if not knobs:
            return dict(run.params or {})
        try:
            return dict(validate_params(knobs, run.params))
        except ParamsError:
            return dict(run.params or {})

    def _facts_with_run(self, facts: dict[str, Any], run: RunRecord) -> dict[str, Any]:
        return memory.with_run(facts, run.id, family=run.preset_id, knobs=self._full_params(run))

    async def _with_run(self, facts: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = await self._store.get_run(run_id)
        if run is None:
            return memory.with_run(facts, run_id)
        return self._facts_with_run(facts, run)

    # ======================================================================
    # memory
    # ======================================================================
    async def _save_facts(self, owner: str, thread_id: str, facts: dict[str, Any]) -> None:
        await self._threads.get_or_create(owner, thread_id)
        await self._threads.update_facts(owner, thread_id, facts)

    async def _fold(self, owner: str, thread_id: str, session_id: str) -> None:
        """When 14 turns are not in the summary yet, fold the oldest 4 into it, leaving 10 (memory.fold_size). A failure
        keeps the old summary; those turns, and the ones after them, are folded the next time (at most 8 at once)."""
        try:
            thread = await self._threads.load(owner, thread_id, since_summary=True)
            if thread is None:
                return
            size = memory.fold_size(len(thread.turns) // 2)
            if size == 0:
                return
            old = thread.turns[: 2 * size]

            async def cost(usd: float) -> None:
                await self._cost(usd, session_id)

            summary = await memory.make_summary(self._provider, self._settings, thread.summary, old, on_cost=cost)
            if summary:
                await self._threads.update_summary(owner, thread_id, summary, old[-1].seq)
        except Exception:
            log.exception("could not fold the older turns of a thread into its summary")

    # ======================================================================
    # money
    # ======================================================================
    def _call_usd(self, role: RoleName, prompt_chars: int, calls: int = 1) -> float:
        # The highest input and output price across the route's model and its fallback chain: any of them can answer.
        price = self._settings.hold_price_for(self._settings.model_for(role))
        tokens_in = math.ceil(prompt_chars / 3.5) + 16
        return calls * (tokens_in * price.input_per_m + self._settings.max_output_for(role) * price.output_per_m) / 1e6

    def _worst_usd(self, prep: Prepared, *, with_check: bool, fold_turns: int = 0) -> float:
        """The most one message can spend after the fact check: 5 chat calls at their output cap with the full context
        (the facts block, the turns, four tool results and, on a first message, the check), one research (a message runs
        at most one new one) and, when this message can start a fold, two summary calls over the `fold_turns` it takes."""
        if self._settings.provider == "fake":
            return 0.0
        context = len(prep.system) + len(prep.base_user) + len(prep.message) + MAX_STEPS * MAX_TOOL_TEXT
        if with_check:
            context += CHECK_ALLOWANCE_CHARS
        chat = self._call_usd("chat", context, calls=MAX_CALLS)
        research = self._research.worst_case_usd() if self._research is not None else 0.0
        fold = 0.0
        if fold_turns:
            chars = len(memory.SUMMARY_SYSTEM) + 2 * fold_turns * (THREAD_USER_MAX + THREAD_VERITY_MAX) + 1_000
            fold = self._call_usd("memory", chars, calls=2)  # the second call only when the first summary is too short
        return chat + research + fold

    def _lock(self, owner: str, thread_id: str) -> asyncio.Lock:
        if len(self._locks) > 1_000:
            for key in [k for k, held in self._locks.items() if not held.locked()]:
                del self._locks[key]
        return self._locks.setdefault((owner, thread_id), asyncio.Lock())


def _last(steps: Sequence[ToolOutcome], attr: str) -> ToolOutcome | None:
    """The newest step that worked and carries `attr` (proposal, cpu_action, walk, open_tab or research)."""
    return next((s for s in reversed(steps) if s.ok and getattr(s, attr) is not None), None)
