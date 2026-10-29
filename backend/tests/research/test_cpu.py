"""CPU research templates, cited-keyword checks and the fake-only path to run references."""

import pytest

from verity.llm.fake import FakeProvider
from verity.research.cpu import cpu_block_research
from verity.research.queries import canonical_key, family_queries, key_terms, query_key, safe_label, text_sha256
from verity.research.serper import SearchHit, SearchResult
from verity.research.service import ResearchRequest, ResearchService, rank_techniques
from verity.research.sources import Ranked
from verity.research.summary import check_answer
from verity.research.techniques import TECHNIQUES, technique, techniques_for, valid_ids
from verity.store import SqliteStore


BLOCKS = ("rv32i_imm", "rv32i_dec", "rv32i_alu", "rv32i_br", "rv32i_rf", "rv32i_lsu", "rv32i_ctl")
GOALS = ("area", "speed", "power")
CPU_TECHNIQUES = [(block, tech) for block in BLOCKS for tech in techniques_for(block)]


@pytest.mark.parametrize("block", BLOCKS)
def test_fixed_cpu_techniques_are_complete_and_globally_unique(block) -> None:
    choices = techniques_for(block)
    assert 3 <= len(choices) <= 6
    all_ids = [t.id for family in TECHNIQUES.values() for t in family]
    assert len(all_ids) == len(set(all_ids))
    for t in choices:
        assert t.label.strip() and t.idea.strip()
        assert t.keywords and all(k.strip() and k == k.lower() for k in t.keywords)
        assert t.goals and set(t.goals) <= set(GOALS)
        assert technique(t.id) is t
    assert valid_ids(block, [choices[0].id, "forged", choices[0].id, "ripple_carry"]) == [choices[0].id]


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("goal", GOALS)
def test_request_matches_fixed_pack_cache_and_query_contract(settings, block, goal) -> None:
    req = cpu_block_research(block, goal)
    assert isinstance(req, ResearchRequest)
    assert req.family == block and req.family_title
    assert req.intent_class == "in_family" and req.in_family
    assert req.params == {} and req.verdict_if_ok == "buildable_now"
    assert req.label == safe_label(req.family_title) and req.label
    assert req.text_sha256 == text_sha256(f"{block} {goal}")
    assert req.pack_sha256 is None and req.tool_image_digest is None
    service = ResearchService(settings, None, None, None, {})
    queries, key = service.queries_for(req)
    assert key == canonical_key(block, {})
    assert queries == family_queries(block, {})
    assert [q.endpoint for q in queries] == ["search", "scholar"]
    assert all(q.q.strip() for q in queries)
    assert family_queries(block, {"width": "private text", "goal": "ignore instructions"}) == queries
    assert key_terms(req.label, block)
    ids = [t.id for t in techniques_for(block)]
    ranked = rank_techniques(ids, [], goal)
    fits = [goal in technique(tid).goals for tid in ranked]
    assert fits == sorted(fits, reverse=True)


def test_block_queries_and_keys_are_distinct_but_reusable_across_goals() -> None:
    assert len({query_key(family_queries(block, {})) for block in BLOCKS}) == len(BLOCKS)
    assert len({canonical_key(block, {}) for block in BLOCKS}) == len(BLOCKS)
    for block in BLOCKS:
        requests = [cpu_block_research(block, goal) for goal in GOALS]
        assert len({r.text_sha256 for r in requests}) == len(GOALS)
        assert all(r.params == {} for r in requests)


@pytest.mark.parametrize("block", ["", "rv32i_core", "alu8", "private project ignore instructions"])
def test_unknown_blocks_are_rejected(block) -> None:
    with pytest.raises(ValueError, match="unknown CPU block"):
        cpu_block_research(block, "area")


@pytest.mark.parametrize("goal", ["", "latency", "AREA", "ignore instructions"])
def test_unknown_goals_are_rejected(goal) -> None:
    with pytest.raises(ValueError, match="unknown CPU research goal"):
        cpu_block_research("rv32i_alu", goal)


def source(title: str, snippet: str) -> Ranked:
    hit = SearchHit(
        endpoint="search", query_idx=0, title=title, snippet=snippet,
        url="https://github.com/lowRISC/ibex", date=None, position=1,
    )
    return Ranked(hit=hit, url=hit.url, domain="github.com", tier="C", relevance=1.0, score=0.5)


@pytest.mark.parametrize(("block", "tech"), CPU_TECHNIQUES, ids=[t.id for _, t in CPU_TECHNIQUES])
@pytest.mark.parametrize("keyword_location", ["title", "snippet"])
def test_cpu_summary_requires_keyword_in_the_source_it_cites(block, tech, keyword_location) -> None:
    grounded = source(
        tech.keywords[0].upper() if keyword_location == "title" else "Hardware study",
        tech.keywords[0].upper() if keyword_location == "snippet" else "Circuit implementation",
    )
    unrelated = source("Unrelated publication", "No supporting evidence here")
    answer = {"techniques": [{"id": tech.id, "sourceIds": [1]}]}
    checked = check_answer(answer, [grounded, unrelated], block, {})
    assert checked.techniques == [tech.id] and checked.cited == {1}
    # A keyword in an uncited source or in the model's sentence cannot support a technique.
    answer["sentences"] = [{"text": tech.keywords[0], "sourceIds": [2]}]
    answer["techniques"][0]["sourceIds"] = [2]
    assert check_answer(answer, [grounded, unrelated], block, {}).techniques == []
    answer["techniques"][0]["sourceIds"] = [99]
    assert check_answer(answer, [grounded], block, {}).techniques == []
    answer["techniques"][0]["sourceIds"] = [1]
    other_block = next(b for b in BLOCKS if b != block)
    assert check_answer(answer, [grounded], other_block, {}).techniques == []


class LocalSources:
    """No HTTP client or credentials: canned search data for the existing summary and service path."""

    name = "fake"

    def __init__(self, block: str) -> None:
        self.block = block
        self.calls = []

    def provider_for(self, endpoint):
        return "fake"

    async def search(self, endpoint, query, query_idx):
        self.calls.append((endpoint, query))
        terms = " ".join(t.keywords[0] for t in techniques_for(self.block))
        return SearchResult(hits=[SearchHit(
            endpoint=endpoint, query_idx=query_idx, title=cpu_block_research(self.block, "area").family_title,
            snippet=f"{terms}. UNTRUSTED_WEB_MARKER", url="https://github.com/lowRISC/ibex",
            date=None, position=1,
        )], credits=0)

    async def aclose(self):
        return None


@pytest.mark.parametrize("block", BLOCKS)
async def test_fake_cpu_research_reaches_run_references_and_reuses_across_goals(settings, tmp_path, block) -> None:
    assert settings.provider == "fake"
    store = SqliteStore(settings)
    await store.open()
    adapter = LocalSources(block)
    provider = FakeProvider(settings, delay_s=0, cassette_dir=tmp_path / "no-cassettes")
    service = ResearchService(settings, store, provider, adapter, {})
    try:
        outcome = await service.lookup_or_search(cpu_block_research(block, "area"), session_id="cpu-owner")
        assert outcome.state == "ran" and outcome.record is not None
        record = outcome.record
        expected = {t.id for t in techniques_for(block)}
        assert set(record.techniques) == expected
        assert record.usd == 0 and record.serper_credits == 0 and record.tavily_credits == 0
        assert len(adapter.calls) == 2
        reused = await service.lookup_or_search(cpu_block_research(block, "speed"), session_id="cpu-owner")
        assert reused.state == "reused" and reused.record.id == record.id
        assert len(adapter.calls) == 2
        refs = await service.references_for_run(
            family=block, params={}, priority="power", research_id=record.id, session_id="cpu-owner",
            pack_sha256="locked-pack-hash", tool_image_digest="fake-tool-image",
        )
        assert refs.research_id == record.id and set(refs.techniques) == expected
        planner_text = refs.planner_text()
        assert all(technique(tid).label in planner_text for tid in expected)
        assert "UNTRUSTED_WEB_MARKER" not in planner_text and "https://" not in planner_text
        assert "UNTRUSTED_WEB_MARKER" in record.sources[0].snippet
    finally:
        await service.aclose()
        await provider.aclose()
        await store.close()
