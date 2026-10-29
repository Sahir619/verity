"""ResearchService: the fact-check research behind POST /parse, and the references a new run reads.

    lookup_or_search(request, session_id=...)   cached research (by key or by templated queries), else at most 3
                                                Serper calls, ranking in code, one research-route summary, code checks
    references_for_run(...)                     research for a run (researchId, then exact key, then nearest knobs)
                                                plus comparable past outcomes, as technique ids and code-written lines

Money: the summary's cost and the search credits (Serper, Tavily) are added to the daily spend (the fake provider and
the fake search adapter spend nothing). A per-session limit (research.per_session_per_hour) applies to new research
only; reuse is free. A daily credit counter per service stops new searches at research.serper_credits_per_day and
research.tavily_credits_per_day.
"""

import asyncio
import hashlib
import logging
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from ..api.ratelimit import TokenBucketLimiter
from ..config import Settings
from ..contracts import Research, ResearchSentence, ResearchSource, ResearchTechnique
from ..core.errors import LLMError
from ..core.family import Knob
from ..core.protocols import LLMProvider, Store
from ..core.records import ResearchRecord, ResearchSourceRow, RunOutcomeRow, StrategyOutcomeRow
from .queries import Query, canonical_key, family_queries, key_terms, label_queries, query_key
from .serper import ENDPOINTS, SearchAdapter, SearchError, SearchResult
from .sources import rank_hits
from .summary import PROMPT_VERSION, SummaryResult, summarize
from .techniques import Technique, technique, techniques_for, valid_ids

log = logging.getLogger("verity.research")

OutcomeState = Literal["ran", "reused", "failed", "skipped"]


def session_hmac(session_id: str) -> str:
    """What research_grant stores instead of the session id."""
    return hashlib.sha256(f"verity-research|{session_id}".encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    """What /parse knows after the parser call and the code checks. No raw user text except its hash."""

    intent_class: str
    label: str  # already reduced to hardware-vocabulary words (queries.safe_label)
    family: str | None  # the validated family the request maps to (in range) or its closest family
    family_title: str | None
    params: Mapping[str, object] | None  # validated knobs of `family`, defaults filled in
    in_family: bool  # True: an in-family request, researched and cached by family plus knobs
    text_sha256: str
    verdict_if_ok: str  # the verdict code gives when the research does not fail
    pack_sha256: str | None = None  # of the pack at these knobs, for counting comparable past runs
    tool_image_digest: str | None = None


@dataclass(slots=True)
class ResearchOutcome:
    state: OutcomeState
    research: Research | None = None
    record: ResearchRecord | None = None
    why: str = ""  # skipped: rate_limited | credits | no_queries | busy


@dataclass(slots=True)
class RunReferences:
    """What a run reads before planning. Only technique ids, our own labels and code-written numbers."""

    key: str
    research_id: str | None = None
    techniques: list[str] = field(default_factory=list)  # ranked ids from the family's fixed list
    past_lines: list[str] = field(default_factory=list)
    past_runs: int = 0

    def planner_text(self) -> str:
        """The "Known approaches" section of the plan prompt ("" when there is nothing to say)."""
        lines: list[str] = []
        known = [t for t in (technique(i) for i in self.techniques) if t is not None]
        if known:
            lines += [
                "## Known approaches (from Verity's research)",
                "Well-known ways to build this kind of block. Use one only if it fits the line limit; the interface "
                "and the cycle latency stay exactly as given.",
            ]
            lines += [f"- {t.label}: {t.idea}." for t in known]
        if self.past_lines:
            if lines:
                lines.append("")
            lines.append("## Past Verity runs on this exact design (same task pack and tool image)")
            lines += self.past_lines
        return "\n".join(lines)

    def explore_idea(self, used: set[str]) -> Technique | None:
        """One technique the parent does not use (the EXPLORE child's idea), or None."""
        for tid in self.techniques:
            if tid not in used:
                return technique(tid)
        return None


def knob_distance(knobs: list[Knob], a: Mapping[str, object], b: Mapping[str, object]) -> float:
    """Sum over int knobs of |a - b| / (max - min), plus 1 per bool or bits mismatch."""
    total = 0.0
    for knob in knobs:
        x, y = a.get(knob.id, knob.default), b.get(knob.id, knob.default)
        if knob.kind == "int" and isinstance(x, int) and isinstance(y, int):
            span = (knob.max or 0) - (knob.min or 0)
            total += abs(x - y) / span if span > 0 else float(x != y)
        elif x != y:
            total += 1.0
    return total


class ResearchService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        provider: LLMProvider,
        adapter: SearchAdapter,
        families: Mapping[str, list[Knob]],
    ) -> None:
        self._settings = settings
        self._store = store
        self._provider = provider
        self._adapter = adapter
        self._families = dict(families)
        cfg = settings.research
        self._limiter = TokenBucketLimiter(max(1, cfg.per_session_per_hour), 3600.0)
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def adapter_name(self) -> str:
        return self._adapter.name

    @property
    def providers(self) -> frozenset[str]:
        """The paid services behind the adapter ("serper", "tavily"); empty for the fake adapter."""
        return frozenset(p for p in (self._adapter.provider_for(e) for e in ENDPOINTS) if p in self._usd_per_credit)

    @property
    def _usd_per_credit(self) -> dict[str, float]:
        cfg = self._settings.research
        return {"serper": cfg.usd_per_credit, "tavily": cfg.tavily_usd_per_credit}

    async def aclose(self) -> None:
        await self._adapter.aclose()

    def worst_case_usd(self) -> float:
        """The most one research can spend (summary at its output cap plus every search credit)."""
        if self._settings.provider == "fake":
            return 0.0
        price = self._settings.hold_price_for(self._settings.model_for("research"))  # dearest of the primary and its chain
        llm = (6_000 * price.input_per_m + self._settings.max_output_for("research") * price.output_per_m) / 1e6
        rates = [self._usd_per_credit[p] for p in self.providers]
        search = self._settings.research.max_queries * max(rates, default=0.0)
        return llm + search

    # ======================================================================
    # fact check
    # ======================================================================
    def queries_for(self, req: ResearchRequest) -> tuple[list[Query], str | None]:
        """(queries, key). In-family requests use family templates and the family-plus-knobs key."""
        params = dict(req.params or {})
        if req.in_family and req.family:
            queries = family_queries(req.family, params)
            key: str | None = canonical_key(req.family, params)
        else:
            queries = label_queries(req.intent_class, req.label, req.family, params)
            key = None
        return queries[: self._settings.research.max_queries], key

    async def is_saved(self, req: ResearchRequest) -> bool:
        """True when lookup_or_search would reuse saved research for this request: no search, no spend."""
        queries, key = self.queries_for(req)
        return bool(queries) and await self._cached(key, query_key(queries)) is not None

    async def lookup_or_search(self, req: ResearchRequest, *, session_id: str) -> ResearchOutcome:
        queries, key = self.queries_for(req)
        if not queries:
            return ResearchOutcome(state="skipped", why="no_queries")
        qkey = query_key(queries)
        if len(self._locks) > 1_000:
            for name in [n for n, held in self._locks.items() if not held.locked()]:
                del self._locks[name]
        lock = self._locks.setdefault(key or qkey, asyncio.Lock())  # one search per key at a time: no double spend
        async with lock:
            cached = await self._cached(key, qkey)
            if cached is not None:
                await self._grant(cached.id, session_id)
                past = await self._past_runs(req)
                return ResearchOutcome(
                    state="reused", research=self.to_wire(cached, reused=True, past_runs=past), record=cached
                )
            if self._limiter.acquire(f"research:{session_id}") > 0:
                return ResearchOutcome(state="skipped", why="rate_limited")
            day = _utcnow().strftime("%Y-%m-%d")
            if await self._over_credit_cap(queries, day):
                self._limiter.refund(f"research:{session_id}")
                return ResearchOutcome(state="skipped", why="credits")
            record = await self._search(req, queries, key, qkey)
            await self._record_spend(day, record, session_id)
            await self._store.put_research(record)
            await self._grant(record.id, session_id)
            past = await self._past_runs(req)
            state: OutcomeState = "failed" if record.status == "failed" else "ran"
            return ResearchOutcome(
                state=state,
                research=self.to_wire(record, reused=False, past_runs=past),
                record=record,
            )

    async def _over_credit_cap(self, queries: list[Query], day: str) -> bool:
        """True when these queries would take a service past its credits for the day (one credit per call)."""
        cfg = self._settings.research
        caps = {"serper": cfg.serper_credits_per_day, "tavily": cfg.tavily_credits_per_day}
        calls = Counter(self._adapter.provider_for(q.endpoint) for q in queries)
        for provider, n in calls.items():
            if provider not in caps:
                continue
            used = await (self._store.serper_credits(day) if provider == "serper" else self._store.tavily_credits(day))
            if used + n > caps[provider]:
                return True
        return False

    async def _cached(self, key: str | None, qkey: str) -> ResearchRecord | None:
        now = _utcnow()
        if key is not None:
            return await self._store.research_by_key(key, now)
        return await self._store.research_by_query_key(qkey, now)

    async def _grant(self, research_id: str, session_id: str) -> None:
        if session_id:
            await self._store.grant_research(research_id, session_hmac(session_id))

    async def _record_spend(self, day: str, record: ResearchRecord, session_id: str) -> None:
        try:
            usd = 0.0
            if self._settings.provider != "fake":
                usd += record.llm_usd
            if record.serper_credits:
                usd += record.serper_credits * self._settings.research.usd_per_credit
                await self._store.add_serper_credits(day, record.serper_credits)
            if record.tavily_credits:
                usd += record.tavily_credits * self._settings.research.tavily_usd_per_credit
                await self._store.add_tavily_credits(day, record.tavily_credits)
            if usd > 0:
                await self._store.add_daily_spend(day, usd, session_id)
        except Exception:
            log.exception("research: could not record the spend")

    async def _past_runs(self, req: ResearchRequest) -> int:
        if not (req.family and req.params is not None and req.pack_sha256 and req.tool_image_digest is not None):
            return 0
        try:
            rows = await self._store.run_outcomes(
                canonical_key(req.family, dict(req.params)), req.pack_sha256, req.tool_image_digest, limit=100
            )
        except Exception:
            log.exception("research: could not count past runs")
            return 0
        return len(rows)

    async def _search(self, req: ResearchRequest, queries: list[Query], key: str | None, qkey: str) -> ResearchRecord:
        cfg = self._settings.research
        results = await asyncio.gather(
            *(self._adapter.search(q.endpoint, q.q, i) for i, q in enumerate(queries)), return_exceptions=True
        )
        hits = []
        credits: Counter[str] = Counter()  # what each paid service billed, by provider
        failures = 0
        for query, outcome in zip(queries, results):
            if isinstance(outcome, SearchResult):
                hits.extend(outcome.hits)
                credits[self._adapter.provider_for(query.endpoint)] += outcome.credits
            elif isinstance(outcome, SearchError):
                failures += 1
                log.warning("research: a search failed (%s)", outcome.kind)
            elif isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                failures += 1
                log.warning("research: a search failed unexpectedly (%s)", type(outcome).__name__)

        family_for_terms = req.family
        ranked = rank_hits(
            hits,
            key_terms(req.label, family_for_terms),
            max_sources=cfg.max_sources,
            per_domain=cfg.per_domain,
            snippet_chars=cfg.snippet_chars,
        )
        summary = SummaryResult()
        status: Literal["ok", "partial", "failed"] = "ok"
        if failures == len(queries):
            status = "failed"
        elif ranked:
            try:
                summary = await summarize(
                    self._provider,
                    self._settings,
                    intent_class=req.intent_class,
                    label=req.label,
                    family=req.family,
                    family_title=req.family_title,
                    knob_values=dict(req.params or {}),
                    sources=ranked,
                )
            except LLMError as exc:
                log.warning("research: the summary call failed: %s", exc)
                status = "failed"
            else:
                if not summary.readable:
                    status = "partial"
        if status == "ok" and failures:
            status = "partial"

        now = _utcnow()
        sources = [
            ResearchSourceRow(
                n=n,
                url=r.url,
                domain=r.domain,
                tier=r.tier,  # type: ignore[arg-type]
                title=r.hit.title,
                endpoint=r.hit.endpoint,
                query_idx=r.hit.query_idx,
                rank=r.hit.position,
                snippet=r.hit.snippet,
                date=r.hit.date,
                score=r.score,
                relevance=r.relevance,
                cited=n in summary.cited,
            )
            for n, r in enumerate(ranked, start=1)
        ]
        llm_usd = summary.usd if self._settings.provider != "fake" else 0.0  # the fake provider spends nothing
        serper_credits, tavily_credits = credits["serper"], credits["tavily"]
        search_usd = serper_credits * cfg.usd_per_credit + tavily_credits * cfg.tavily_usd_per_credit
        return ResearchRecord(
            id="rs_" + uuid.uuid4().hex,
            created_at=now,
            expires_at=now + timedelta(days=cfg.ttl_days),
            key=key,
            query_key=qkey,
            family=req.family,
            params=dict(req.params) if req.params is not None else None,
            intent_class=req.intent_class,
            intent_label=req.label,
            text_sha256=req.text_sha256,
            verdict="could_not_check" if status == "failed" else req.verdict_if_ok,
            status=status,
            queries=[{"endpoint": q.endpoint, "q": q.q} for q in queries],
            sentences=summary.sentences if status != "failed" else [],
            techniques=valid_ids(req.family, summary.techniques) if status != "failed" else [],
            done_before=summary.done_before if status != "failed" else False,
            model=summary.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
            llm_usd=round(llm_usd, 6),
            serper_credits=serper_credits,
            tavily_credits=tavily_credits,
            usd=round(llm_usd + search_usd, 6),
            latency_ms=summary.latency_ms,
            sources=sources,
        )

    def to_wire(self, record: ResearchRecord, *, reused: bool, past_runs: int) -> Research:
        """usd and serperCredits are what THIS request spent: 0 when the record was reused."""
        labels = {t.id: t.label for t in techniques_for(record.family)}
        return Research(
            id=record.id,
            status=record.status,
            reused=reused,
            created_at=_iso_z(record.created_at),
            queries=[q.get("q", "") for q in record.queries],
            sentences=[
                ResearchSentence(text=s["text"], source_ids=list(s.get("sourceIds", [])))
                for s in record.sentences
                if isinstance(s, dict) and isinstance(s.get("text"), str)
            ],
            sources=[
                ResearchSource(
                    n=s.n, url=s.url, title=s.title, domain=s.domain, tier=s.tier, date=s.date, endpoint=s.endpoint
                )
                for s in record.sources
            ],
            techniques=[ResearchTechnique(id=t, label=labels[t]) for t in record.techniques if t in labels],
            done_before=record.done_before,
            past_runs=past_runs,
            usd=0.0 if reused else record.usd,
            serper_credits=0 if reused else record.serper_credits,
            tavily_credits=(0 if reused else record.tavily_credits) if "tavily" in self.providers else None,
        )

    async def get(self, research_id: str) -> ResearchRecord | None:
        return await self._store.get_research(research_id)

    # ======================================================================
    # references for a run
    # ======================================================================
    async def references_for_run(
        self,
        *,
        family: str,
        params: Mapping[str, object],
        priority: str,
        research_id: str | None,
        session_id: str,
        pack_sha256: str,
        tool_image_digest: str,
    ) -> RunReferences:
        """Research by researchId (owned by this session and matching the family and knobs; a mismatch is ignored),
        else the exact key, else the nearest knobs of the same family (distance research.nearest_distance or less).
        Past outcomes count only with the same pack hash and tool image digest."""
        key = canonical_key(family, dict(params))
        refs = RunReferences(key=key)
        record = await self._research_for_run(family, params, key, research_id, session_id)
        try:
            past = await self._store.run_outcomes(key, pack_sha256, tool_image_digest, limit=20)
            strategies = await self._store.strategy_outcomes(key, pack_sha256, tool_image_digest)
        except Exception:
            log.exception("research: could not read past outcomes")
            past, strategies = [], []
        refs.past_runs = len(past)
        if record is not None:
            refs.research_id = record.id
            refs.techniques = rank_techniques(valid_ids(family, record.techniques), strategies, priority)
        refs.past_lines = past_lines(past, strategies)
        return refs

    async def _research_for_run(
        self, family: str, params: Mapping[str, object], key: str, research_id: str | None, session_id: str
    ) -> ResearchRecord | None:
        if research_id:
            try:
                record = await self._store.get_research(research_id)
                owned = record is not None and await self._store.research_granted(record.id, session_hmac(session_id))
            except Exception:
                log.exception("research: could not read research %s", research_id)
                record, owned = None, False
            if record is not None and owned and record.family == family and record.params is not None:
                if canonical_key(record.family, record.params) == key:
                    return record
            log.info("run ignores research id %s (not owned, or another chip or knobs)", research_id)
        now = _utcnow()
        exact = await self._store.research_by_key(key, now)
        if exact is not None:
            return exact
        knobs = self._families.get(family)
        if not knobs:
            return None
        best: tuple[float, ResearchRecord] | None = None
        for cand in await self._store.research_for_family(family, now):
            if cand.params is None:
                continue
            dist = knob_distance(knobs, params, cand.params)
            if dist <= self._settings.research.nearest_distance and (best is None or dist < best[0]):
                best = (dist, cand)
        return best[1] if best is not None else None


def rank_techniques(ids: list[str], strategies: list[StrategyOutcomeRow], priority: str) -> list[str]:
    """Techniques that beat v1 before (our own measurements) first, then those that serve the run's goal (the fixed
    list's `goals` tag), then the research order."""
    wins: dict[str, int] = {}
    for row in strategies:
        if row.technique and row.result == "improved":
            wins[row.technique] = wins.get(row.technique, 0) + 1
    order = {tid: i for i, tid in enumerate(ids)}

    def fits(tid: str) -> int:
        tech = technique(tid)
        return 0 if tech is not None and priority in tech.goals else 1

    return sorted(ids, key=lambda t: (-wins.get(t, 0), fits(t), order[t]))


def past_lines(past: list[RunOutcomeRow], strategies: list[StrategyOutcomeRow], limit: int = 4) -> list[str]:
    """Code-written lines about comparable past runs. Every number is a measured ratio against that run's own v1."""
    if not past:
        return []
    done = sum(1 for p in past if not p.partial)
    lines = [f"- {len(past)} earlier run(s), {done} finished."]
    better = [s for s in strategies if s.result == "improved" and s.area_rel is not None and s.clock_rel is not None]
    better.sort(key=lambda s: (s.area_rel or 9.0) * (s.clock_rel or 9.0))
    for s in better[:limit]:
        tech = technique(s.technique) if s.technique else None
        idea = f", idea: {tech.label}" if tech else ""
        lines.append(
            f"- A {s.strategy.lower()} version{idea} measured area {s.area_rel:.2f}x and clock period "
            f"{s.clock_rel:.2f}x of its run's v1."
        )
    if len(lines) == 1:
        lines.append("- No earlier version beat its run's v1.")
    return lines


__all__ = [
    "ResearchOutcome",
    "ResearchRequest",
    "ResearchService",
    "RunReferences",
    "knob_distance",
    "past_lines",
    "rank_techniques",
    "session_hmac",
]
