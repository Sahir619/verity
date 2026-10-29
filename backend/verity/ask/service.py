"""AskService: one turn of POST /runs/{id}/ask (plan claim 3). The RunManager checks the thread state first (owner
session, run ended) and hands the run here.

    load the run card (code) -> free keyword pre-filter -> [router call] -> dispatch:
        run_question  selection | drop | timing | cost   code template, no model call
                      area | power | tests | other       chat route with the facts block, numbers checked, else template
        rtl_question                                     chat route with the version's Verilog as data, Verilog stripped
        change_request                                   propose_run (one parser-route call, then validate_params)
        research                                         the run's research, or the research service for its knobs
        walkthrough                                      the nine steps, built by code from the run, no model call
        new_chip | small_talk | off_topic                fixed replies

Money: a turn that may call a model first reserves its worst case on the daily budget and on the session's (router at
its output cap plus the largest of two chat calls, one proposal call or one research), released after the turn; every
call's real cost is added to the daily spend and to the session's. A turn answered by a template costs nothing and reserves nothing, so it still works when the
daily budget is used up. The last three turns of each (session, run) are kept in memory only, for the chat prompt.
"""

import logging
import math
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from ..config import RoleName, Settings
from ..contracts import AskAnswer, AskNewChip, AskRefused, AskReply, Priority, Research
from ..core.family import Knob, ParamsError, validate_params
from ..core.pack import TaskPack
from ..core.protocols import LLMProvider, Store
from ..core.records import RunRecord
from ..research.queries import safe_label, text_sha256
from ..research.service import ResearchRequest, ResearchService
from . import reply
from .intent import TEMPLATED_TOPICS, Routed, mentioned_versions, prefilter, route, router_prompt_chars
from .propose import propose_run
from .tools import RunCard, load_card
from .walkthrough import build_walkthrough

log = logging.getLogger("verity.ask")

HISTORY_TURNS = 3
HISTORY_KEYS = 1_000
_PROPOSE_PROMPT_CHARS = 3_000
_NO_MODEL_INTENTS = frozenset({"small_talk", "new_chip", "off_topic", "walkthrough"})

Hold = Callable[[float, str], AbstractAsyncContextManager[None]]  # (worst case in dollars, session id)


class AskService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        provider: LLMProvider,
        packs: Mapping[str, TaskPack],
        families: Mapping[str, list[Knob]],
        research: ResearchService | None,
        *,
        hold: Hold,
        record: Callable[[float, str], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._store = store
        self._provider = provider
        self._packs = dict(packs)
        self._families = dict(families)
        self._research = research
        self._hold = hold  # reserves a worst case on the daily and session budgets; raises AdmissionDenied
        self._record = record  # adds a real cost to the daily and session spend (the fake provider spends nothing)
        self._history: OrderedDict[tuple[str, str], deque[tuple[str, str]]] = OrderedDict()

    # ======================================================================
    # one turn
    # ======================================================================
    async def ask(self, run: RunRecord, *, session_id: str, text: str, focus_version: str | None = None) -> AskReply:
        card = await load_card(self._store, run)
        pre = prefilter(text, run.preset_id)
        missing = [lbl for lbl in mentioned_versions(text) if lbl not in card.labels]
        if missing and (pre is None or pre.intent in ("run_question", "rtl_question", "walkthrough")):
            answer: AskReply = reply.unknown_version_answer(card, missing)
            self._remember(session_id, run.id, text, answer)
            return answer

        free = pre is not None and (
            pre.intent in _NO_MODEL_INTENTS or (pre.intent == "run_question" and pre.topic in TEMPLATED_TOPICS)
        )
        if pre is not None and free:
            answer = await self._dispatch(pre, card, text, session_id, focus_version)
        else:
            async with self._hold(self._worst_usd(card, pre, text), session_id):
                cost = self._cost_for(session_id)
                routed = pre or await route(text, card, self._provider, self._settings, on_cost=cost)
                answer = await self._dispatch(routed, card, text, session_id, focus_version)
        self._remember(session_id, run.id, text, answer)
        return answer

    async def _dispatch(self, routed: Routed, card: RunCard, text: str, session_id: str, focus_version: str | None) -> AskReply:
        labels = [lbl for lbl in routed.versions if lbl in card.labels]
        if not labels and focus_version in card.labels:
            labels = [focus_version]  # type: ignore[list-item]
        intent, topic = routed.intent, routed.topic
        if intent == "off_topic":
            return AskRefused(text=reply.REFUSED_TEXT)
        if intent == "small_talk":
            return reply.SMALL_TALK.model_copy()
        if intent == "new_chip":
            return AskNewChip(text=reply.NEW_CHIP_TEXT)
        if intent == "walkthrough":
            return build_walkthrough(card, labels[0] if labels else None)
        if intent == "change_request":
            return await propose_run(
                text,
                card,
                packs=self._packs,
                families=self._families,
                llm=self._provider,
                settings=self._settings,
                on_cost=self._cost_for(session_id),
                idea_for=lambda family, params, goal: self._idea_for(card.run, family, params, goal, session_id),
            )
        if intent == "research":
            return await self._research_answer(card, text, session_id)
        if intent == "run_question" and topic == "selection":
            return reply.selection_answer(card)
        if intent == "run_question" and topic == "drop":
            return reply.drop_answer(card, labels)
        if intent == "run_question" and topic == "timing":
            return reply.timing_answer(card, labels)
        if intent == "run_question" and topic == "cost":
            return reply.cost_answer(card)
        rtl = intent == "rtl_question"
        # The version the user has open leads, then any others the text names, so the basis line names the open one first.
        shown = [focus_version, *(lbl for lbl in labels if lbl != focus_version)] if focus_version in card.labels else labels
        facts = reply.build_facts(card, shown, include_rtl=rtl)
        focus = [c for c in (card.by_label(lbl) for lbl in shown) if c is not None]
        return await reply.chat_answer(
            text,
            facts,
            self._turns(session_id, card.run.id),
            self._provider,
            self._settings,
            on_cost=self._cost_for(session_id),
            fallback=reply.facts_answer(card, topic, labels),
            basis=lambda served: reply.chat_basis(
                self._settings, focus, sample=getattr(self._provider, "name", "") == "fake", model=served
            ),
        )

    def _cost_for(self, session_id: str) -> Callable[[float], Awaitable[None]]:
        """The cost callback the model calls of this turn report to, charged to this session as well as the day."""

        async def record(usd: float) -> None:
            await self._record(usd, session_id)

        return record

    # ======================================================================
    # research and the proposal's idea
    # ======================================================================
    async def _research_answer(self, card: RunCard, text: str, session_id: str) -> AskAnswer:
        if self._research is None:
            return AskAnswer(text="Research is off on this server.", bullets=[], basis="No research ran.", templated=True)
        run = card.run
        research: Research | None = None
        state = "failed"
        try:
            record = await self._research.get(run.research_id) if run.research_id else None
            if record is not None:
                research, state = self._research.to_wire(record, reused=True, past_runs=0), "reused"
            else:
                outcome = await self._research.lookup_or_search(self._research_request(card, text), session_id=session_id)
                research, state = outcome.research, outcome.state
        except Exception:
            log.exception("ask: research failed")
        return research_reply(research, state)

    def _research_request(self, card: RunCard, text: str) -> ResearchRequest:
        run = card.run
        knobs = self._families.get(run.preset_id)
        params: dict[str, Any] | None = None
        if knobs:
            try:
                params = dict(validate_params(knobs, run.params))
            except ParamsError:
                params = None
        pack = self._packs.get(run.preset_id)
        return ResearchRequest(
            intent_class="in_family",
            label=safe_label(card.title),
            family=run.preset_id,
            family_title=pack.title if pack is not None else card.title,
            params=params,
            in_family=params is not None,
            text_sha256=text_sha256(text),
            verdict_if_ok="buildable_now",
            pack_sha256=run.task_pack_sha256,
            tool_image_digest=run.tool_image_digest,
        )

    async def _idea_for(
        self, run: RunRecord, family: str, params: Mapping[str, object], priority: Priority, session_id: str
    ) -> str | None:
        """The technique the new run's EXPLORE version tries first: the research that run will read decides it."""
        if self._research is None:
            return None
        try:
            refs = await self._research.references_for_run(
                family=family,
                params=params,
                priority=priority,
                research_id=None,
                session_id=session_id,
                pack_sha256=run.task_pack_sha256,
                tool_image_digest=run.tool_image_digest,
            )
        except Exception:
            log.exception("ask: could not read the references for a proposal")
            return None
        tech = refs.explore_idea(set())
        return tech.label if tech is not None else None

    # ======================================================================
    # money
    # ======================================================================
    def _call_usd(self, role: RoleName, prompt_chars: int, calls: int = 1) -> float:
        # The highest input and output price across the route's model and its fallback chain: any of them can answer.
        price = self._settings.hold_price_for(self._settings.model_for(role))
        tokens_in = math.ceil(prompt_chars / 3.5) + 16
        return calls * (tokens_in * price.input_per_m + self._settings.max_output_for(role) * price.output_per_m) / 1e6

    def _worst_usd(self, card: RunCard, pre: Routed | None, text: str) -> float:
        """The most this turn can spend: router (when the pre-filter did not decide) plus the largest path."""
        if self._settings.provider == "fake":
            return 0.0
        labels = list(pre.versions) if pre is not None else mentioned_versions(text)
        facts = reply.build_facts(card, labels, include_rtl=True)
        # + 4,000 characters for the last turns (3 turns, 600 characters each at most) and the retry note
        chat = self._call_usd("chat", reply.chat_prompt_chars(facts, text, []) + 4_000, calls=2)
        propose = self._call_usd("parser", _PROPOSE_PROMPT_CHARS + len(text))
        research = self._research.worst_case_usd() if self._research is not None else 0.0
        if pre is None:
            return self._call_usd("router", router_prompt_chars(card) + len(text)) + max(chat, propose, research)
        if pre.intent == "change_request":
            return propose
        if pre.intent == "research":
            return research
        return chat

    # ======================================================================
    # the last turns (memory only)
    # ======================================================================
    def _turns(self, session_id: str, run_id: str) -> list[tuple[str, str]]:
        return list(self._history.get((session_id, run_id), ()))

    def _remember(self, session_id: str, run_id: str, question: str, answer: AskReply) -> None:
        key = (session_id, run_id)
        turns = self._history.pop(key, None) or deque(maxlen=HISTORY_TURNS)
        turns.append((question[:300], answer.text[:300]))
        self._history[key] = turns
        while len(self._history) > HISTORY_KEYS:
            self._history.popitem(last=False)


def research_reply(research: Research | None, state: str) -> AskAnswer:
    """The research for this chip as an answer: code-checked sentences with their source numbers, techniques, sources."""
    if research is None or research.status == "failed":
        why = "Research is paused for now: its limit was reached." if state == "skipped" else "Research could not run right now."
        return AskAnswer(text=why, bullets=[], basis="No research ran.", templated=True)
    lines = [f"{s.text} ({', '.join(f'S{n}' for n in s.source_ids)})" if s.source_ids else s.text for s in research.sentences]
    text = lines[0] if lines else f"Found {len(research.sources)} sources about this design."
    bullets = lines[1:3]
    if research.techniques:
        bullets.append("Known approaches: " + ", ".join(t.label for t in research.techniques) + ".")
    for src in research.sources[:3]:
        date = f", {src.date}" if src.date else ""
        bullets.append(f"S{src.n} {src.title} ({src.domain}{date}): {src.url}")
    searched = "; ".join(research.queries)
    basis = f"From web research: {len(research.sources)} sources" + (f", searched: {searched}" if searched else "") + "."
    if research.reused:
        basis += f" Saved research from {research.created_at[:10]}."
    return AskAnswer(text=text, bullets=bullets[:6], basis=basis, templated=not research.sentences)
