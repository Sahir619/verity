"""Tavily search: the adapter against mocked HTTP (shape of the request, 401, 429, 432, 5xx, timeout, empty results), the
key choice between Tavily and Serper, the production host guard, key redaction, and credit accounting in the service.
No network and no key: every response here is shaped like the Tavily docs (docs.tavily.com, search endpoint)."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from verity.api.redact import redact
from verity.config import load_settings
from verity.contracts import ParseInRange
from verity.core.parse import live_families
from verity.llm.fake import FakeProvider
from verity.research import (
    FakeSearchAdapter,
    ResearchService,
    RoutedSearchAdapter,
    SerperAdapter,
    TavilyAdapter,
    build_search_adapter,
)
from verity.research.serper import SearchError, SearchResult
from verity.research.sources import TIER_A, include_domains, tier_for
from verity.research.tavily import credits_used, parse_results
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings

KEY = "tvly-test-key-0123456789"

# The documented 200 response (docs.tavily.com/documentation/api-reference/endpoint/search).
OK_BODY = {
    "query": "adder architecture",
    "results": [
        {
            "title": "Adder (electronics) - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Adder_(electronics)",
            "content": "An adder is a digital circuit that performs addition of numbers.",
            "score": 0.81025416,
            "raw_content": None,
            "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
            "id": "a3f9c2-04",
        },
        {"title": "No url here", "content": "dropped"},
        "not an object",
        {
            "title": "Carry lookahead adders",
            "url": "https://arxiv.org/abs/2401.00001",
            "content": "Carry lookahead adders trade area for delay.",
            "score": 0.7,
        },
    ],
    "response_time": 1.67,
    "usage": {"credits": 1},
    "request_id": "123e4567-e89b-12d3-a456-426614174111",
}


def adapter(handler, seen: list | None = None, **kw) -> TavilyAdapter:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return TavilyAdapter(KEY, base_url="https://api.tavily.com", num=10, timeout_s=8.0, client=client, **kw)


def reply(status: int, body=None):
    return lambda request: httpx.Response(status, json=body if body is not None else {"detail": {"error": "x"}})


# ---------- the request ----------
async def test_the_request_is_a_bearer_post_to_search_with_our_allowlist() -> None:
    seen: list[httpx.Request] = []
    tavily = adapter(reply(200, OK_BODY), seen)
    result = await tavily.search("search", "adder architecture", 2)
    await tavily.aclose()
    request = seen[0]
    assert request.method == "POST" and str(request.url) == "https://api.tavily.com/search"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body == {
        "query": "adder architecture",
        "search_depth": "basic",  # 1 credit
        "max_results": 10,
        "include_domains": include_domains("search"),
        "include_published_date": True,
        "include_usage": True,
    }
    assert "include_answer" not in body and KEY not in request.content.decode()  # Tavily's own answer is never used
    assert [h.url for h in result.hits] == ["https://en.wikipedia.org/wiki/Adder_(electronics)", "https://arxiv.org/abs/2401.00001"]
    assert [h.position for h in result.hits] == [1, 4]  # the rank in Tavily's list; junk entries are skipped
    assert result.hits[0].date == "2025-03-11" and result.hits[1].date is None
    assert result.hits[0].snippet.startswith("An adder is") and all(h.query_idx == 2 and h.endpoint == "search" for h in result.hits)
    assert result.credits == 1


async def test_used_alone_it_serves_scholar_and_patents_with_their_own_domains() -> None:
    seen: list[httpx.Request] = []
    tavily = adapter(reply(200, OK_BODY), seen)
    scholar = await tavily.search("scholar", "carry lookahead adder comparison", 0)
    await tavily.search("patents", "adder circuit", 1)
    await tavily.aclose()
    assert json.loads(seen[0].content)["include_domains"] == list(TIER_A)
    assert json.loads(seen[1].content)["include_domains"] == ["patents.google.com"]
    assert all(h.endpoint == "scholar" for h in scholar.hits)  # the hit keeps the endpoint of the query that asked


def test_every_domain_we_send_is_one_the_allowlist_accepts() -> None:
    for endpoint in ("search", "scholar", "patents"):
        domains = include_domains(endpoint)
        assert domains and len(domains) == len(set(domains)) and len(domains) < 300  # Tavily allows 300
        for domain in domains:
            assert tier_for(f"https://{domain}/page", endpoint) is not None, domain
    assert "patents.google.com" not in include_domains("search")  # patents come from the patents endpoint only


def test_the_cost_is_what_tavily_reports_else_one_credit() -> None:
    assert credits_used({"usage": {"credits": 2}}) == 2
    assert credits_used({"usage": {"credits": 1.2}}) == 2
    assert credits_used({"results": []}) == 1 and credits_used({"usage": {"credits": -3}}) == 1
    assert credits_used({"usage": {"credits": True}}) == 1 and credits_used("junk") == 1


# ---------- errors ----------
@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate_limited"), (432, "plan_limit"), (433, "plan_limit"), (400, "bad_request"),
     (422, "bad_request"), (500, "server"), (503, "server")],
)
async def test_http_errors_become_search_errors_without_the_key(status: int, kind: str) -> None:
    tavily = adapter(reply(status, {"detail": {"error": f"secret {KEY} echoed"}}))
    with pytest.raises(SearchError) as err:
        await tavily.search("search", "adder", 0)
    await tavily.aclose()
    assert err.value.kind == kind and f"HTTP {status}" in str(err.value)
    assert KEY not in str(err.value) and "echoed" not in str(err.value)  # built from the status code only


async def test_timeouts_and_network_errors_and_junk_bodies() -> None:
    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    for handler, kind in ((times_out, "timeout"), (unreachable, "network"), (lambda r: httpx.Response(200, text="<html>"), "bad_response")):
        tavily = adapter(handler)
        with pytest.raises(SearchError) as err:
            await tavily.search("search", "adder", 0)
        await tavily.aclose()
        assert err.value.kind == kind and KEY not in str(err.value)


async def test_empty_and_odd_results_are_no_hits_not_an_error() -> None:
    for body in ({"results": [], "usage": {"credits": 1}}, {"query": "x"}, {"results": "nope"}, [1, 2]):
        tavily = adapter(reply(200, body))
        result = await tavily.search("search", "adder", 0)
        await tavily.aclose()
        assert result.hits == [] and result.credits >= 1  # a successful call is billed even when it finds nothing
    assert parse_results("search", 0, {"results": [{"title": "t", "url": "https://arxiv.org/abs/1", "content": 5}]})[0].snippet == ""


def test_the_adapter_never_shows_its_key() -> None:
    assert KEY not in repr(adapter(reply(200)))


# ---------- redaction ----------
def test_a_tavily_key_is_redacted_in_a_header_and_anywhere_else() -> None:
    assert KEY not in redact(f"Authorization: Bearer {KEY}")
    assert KEY not in redact(f"headers={{'Authorization': 'Bearer {KEY}'}}")
    assert KEY not in redact(f"could not search with key {KEY} today")
    assert redact(f"key {KEY}") == "key tvly-[redacted]"


# ---------- which keys: Tavily for the web, Serper for Scholar and Patents ----------
def keyed(tmp_path, *, serper: bool, tavily: bool, provider: str = "nebius", adapter_name: str = "auto"):
    base = make_settings(tmp_path)
    research = base.research.model_copy(
        update={
            "adapter": adapter_name,
            "api_key": SecretStr("serper-test-key") if serper else None,
            "tavily_api_key": SecretStr(KEY) if tavily else None,
        }
    )
    return base.model_copy(update={"provider": provider, "research": research})


def test_the_adapter_follows_the_keys(tmp_path) -> None:
    modes = {
        (False, False): "off",
        (True, False): "serper",
        (False, True): "tavily",
        (True, True): "both",
    }
    for (serper, tavily), expected in modes.items():
        assert keyed(tmp_path, serper=serper, tavily=tavily).research_adapter() == expected
    # The fake LLM provider never spends search credits, whatever keys exist.
    assert keyed(tmp_path, serper=True, tavily=True, provider="fake").research_adapter() == "fake"
    # An explicit choice uses that service only, and needs its key.
    assert keyed(tmp_path, serper=True, tavily=True, adapter_name="serper").research_adapter() == "serper"
    assert keyed(tmp_path, serper=True, tavily=True, adapter_name="tavily").research_adapter() == "tavily"
    assert keyed(tmp_path, serper=True, tavily=False, adapter_name="tavily").research_adapter() == "off"
    assert keyed(tmp_path, serper=True, tavily=True, adapter_name="off").research_adapter() == "off"


def test_build_search_adapter_makes_the_right_adapter(tmp_path) -> None:
    both = build_search_adapter(keyed(tmp_path, serper=True, tavily=True))
    assert isinstance(both, RoutedSearchAdapter) and both.name == "tavily+serper"
    assert both.provider_for("search") == "tavily"
    assert both.provider_for("scholar") == "serper" and both.provider_for("patents") == "serper"
    only_tavily = build_search_adapter(keyed(tmp_path, serper=False, tavily=True))
    assert isinstance(only_tavily, TavilyAdapter) and all(only_tavily.provider_for(e) == "tavily" for e in ("search", "scholar", "patents"))
    assert isinstance(build_search_adapter(keyed(tmp_path, serper=True, tavily=False)), SerperAdapter)
    assert build_search_adapter(keyed(tmp_path, serper=False, tavily=False)) is None
    assert KEY not in repr(both) and "serper-test-key" not in repr(both)


async def test_each_query_goes_to_one_service_when_both_keys_exist() -> None:
    calls: list[tuple[str, str]] = []

    class Tagged(FakeSearchAdapter):
        async def search(self, endpoint, query, query_idx):  # noqa: ANN001, ANN201
            calls.append((self.name, endpoint))
            return await super().search(endpoint, query, query_idx)

    web, other = Tagged(), Tagged()
    web.name, other.name = "tavily", "serper"
    routed = RoutedSearchAdapter(web, other)
    for endpoint in ("search", "scholar", "patents"):
        await routed.search(endpoint, "adder", 0)  # type: ignore[arg-type]
    assert calls == [("tavily", "search"), ("serper", "scholar"), ("serper", "patents")]
    await routed.aclose()


def test_the_key_reaches_settings_from_the_named_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    toml = tmp_path / "c.toml"
    toml.write_text('provider = "nebius"\n', encoding="utf-8")
    settings = load_settings(toml)
    assert settings.has_tavily_key() and settings.research.tavily_api_key is not None
    assert settings.research.tavily_api_key.get_secret_value() == KEY
    assert KEY not in repr(settings.research)  # SecretStr


def test_the_production_guard_pins_the_tavily_host(tmp_path) -> None:
    prod = keyed(tmp_path, serper=False, tavily=True).model_copy(update={"env": "production"})
    moved = prod.model_copy(update={"research": prod.research.model_copy(update={"tavily_base_url": "https://evil.example.com"})})
    assert any("tavily_base_url host" in p for p in moved.production_problems())
    assert not any("tavily_base_url" in p for p in prod.production_problems())
    both = keyed(tmp_path, serper=True, tavily=True).model_copy(update={"env": "production"})
    bad_serper = both.model_copy(update={"research": both.research.model_copy(update={"serper_base_url": "https://evil.example.com"})})
    assert any("serper_base_url host" in p for p in bad_serper.production_problems())
    forced = keyed(tmp_path, serper=True, tavily=False, adapter_name="tavily").model_copy(update={"env": "production"})
    assert any("TAVILY_API_KEY is not set" in p for p in forced.production_problems())


# ---------- the service: credits, caps and spend ----------
class Paid(FakeSearchAdapter):
    """Fake results, one credit per call, billed to the service named in `name`."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    async def search(self, endpoint, query, query_idx):  # noqa: ANN001, ANN201
        result = await super().search(endpoint, query, query_idx)
        return SearchResult(hits=result.hits, credits=1)


async def manager_with(settings, packs, search):
    store = SqliteStore(settings)
    await store.open()
    provider = FakeProvider(settings, delay_s=0)
    research = ResearchService(settings, store, provider, search, live_families(packs))
    manager = RunManager(settings, store, provider, FakeToolRunner(), packs, research=research)
    await manager.start()
    return manager, store, research


def with_research(tmp_path, **update):
    base = make_settings(tmp_path / "data")
    return base.model_copy(update={"research": base.research.model_copy(update=update)})


async def test_tavily_credits_are_counted_priced_and_sent(tmp_path, packs) -> None:
    settings = with_research(tmp_path)
    manager, store, research = await manager_with(settings, packs, Paid("tavily"))
    try:
        reply = await manager.parse("a 16-bit adder", session_id="s1")
        assert isinstance(reply, ParseInRange) and reply.research is not None
        found = reply.research
        assert found.tavily_credits == 2 and found.serper_credits == 0  # a web query and a papers query, both Tavily
        assert found.usd == pytest.approx(2 * settings.research.tavily_usd_per_credit)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert await store.tavily_credits(day) == 2 and await store.serper_credits(day) == 0
        assert await store.daily_spend(day) == pytest.approx(2 * settings.research.tavily_usd_per_credit)
        wire = found.model_dump(mode="json", by_alias=True)
        assert wire["tavilyCredits"] == 2 and wire["serperCredits"] == 0
        again = await manager.parse("a 16-bit adder", session_id="s1")
        assert again.research is not None and again.research.reused and again.research.tavily_credits == 0  # reuse is free
        assert await store.tavily_credits(day) == 2
        assert research.providers == {"tavily"}
    finally:
        await manager.shutdown()
        await store.close()


async def test_with_both_keys_each_service_bills_its_own_queries(tmp_path, packs) -> None:
    settings = with_research(tmp_path)
    routed = RoutedSearchAdapter(Paid("tavily"), Paid("serper"))
    manager, store, research = await manager_with(settings, packs, routed)
    try:
        reply = await manager.parse("a 16-bit adder", session_id="s1")
        found = reply.research
        assert found is not None and found.tavily_credits == 1 and found.serper_credits == 1
        assert found.usd == pytest.approx(settings.research.tavily_usd_per_credit + settings.research.usd_per_credit)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert await store.tavily_credits(day) == 1 and await store.serper_credits(day) == 1
        assert research.providers == {"tavily", "serper"}
    finally:
        await manager.shutdown()
        await store.close()


async def test_the_daily_tavily_cap_stops_new_research_and_leaves_serper_alone(tmp_path, packs) -> None:
    settings = with_research(tmp_path, tavily_credits_per_day=3, per_session_per_hour=10)
    manager, store, _ = await manager_with(settings, packs, Paid("tavily"))
    try:
        first = await manager.parse("a 16-bit adder", session_id="s1")
        assert first.research is not None and first.research.tavily_credits == 2
        over = await manager.parse("an 8-bit counter", session_id="s2")  # 2 used + 2 needed > 3
        assert over.research is None and over.verdict == "could_not_check"
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert await store.tavily_credits(day) == 2
    finally:
        await manager.shutdown()
        await store.close()
    # One Tavily call and one Serper call per research: a Tavily cap of 1 still lets the first research through.
    settings = with_research(tmp_path / "b", tavily_credits_per_day=1, serper_credits_per_day=1)
    manager, store, _ = await manager_with(settings, packs, RoutedSearchAdapter(Paid("tavily"), Paid("serper")))
    try:
        first = await manager.parse("a 16-bit adder", session_id="s1")
        assert first.research is not None
        second = await manager.parse("an 8-bit counter", session_id="s2")
        assert second.research is None
    finally:
        await manager.shutdown()
        await store.close()


async def test_a_search_error_from_tavily_is_a_failed_research_not_a_crash(tmp_path, packs) -> None:
    class Refused(FakeSearchAdapter):
        name = "tavily"

        async def search(self, endpoint, query, query_idx):  # noqa: ANN001, ANN201
            raise SearchError("plan_limit", "the search service says this key's credit limit is used up (HTTP 432)")

    settings = with_research(tmp_path)
    manager, store, _ = await manager_with(settings, packs, Refused())
    try:
        reply = await manager.parse("a 16-bit adder", session_id="s1")
        assert reply.research is not None and reply.research.status == "failed" and reply.verdict == "could_not_check"
        assert reply.research.tavily_credits == 0
    finally:
        await manager.shutdown()
        await store.close()


async def test_the_worst_case_counts_the_dearest_search_service(tmp_path, packs) -> None:
    base = make_settings(tmp_path / "data", provider="nebius")
    settings = base.model_copy(update={"llm": base.llm.model_copy(update={"api_key": SecretStr("test-key")})})
    store = SqliteStore(settings)
    provider = FakeProvider(settings, delay_s=0)
    cfg = settings.research
    families = live_families(packs)
    llm_only = ResearchService(settings, store, provider, FakeSearchAdapter(), families).worst_case_usd()
    serper = ResearchService(settings, store, provider, Paid("serper"), families).worst_case_usd()
    tavily = ResearchService(settings, store, provider, Paid("tavily"), families).worst_case_usd()
    both = ResearchService(settings, store, provider, RoutedSearchAdapter(Paid("tavily"), Paid("serper")), families).worst_case_usd()
    assert serper == pytest.approx(llm_only + cfg.max_queries * cfg.usd_per_credit)
    assert tavily == pytest.approx(llm_only + cfg.max_queries * cfg.tavily_usd_per_credit)
    assert both == pytest.approx(tavily)


async def test_research_is_listed_in_health_only_when_a_search_adapter_exists(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    provider = FakeProvider(settings, delay_s=0)
    off = RunManager(settings, store, provider, FakeToolRunner(), packs)
    on = RunManager(
        settings, store, provider, FakeToolRunner(), packs, research=ResearchService(settings, store, provider, Paid("tavily"), live_families(packs))
    )
    assert "research" not in off.features() and "research" in on.features()
    real = keyed(tmp_path, serper=False, tavily=True)  # a real provider with only a Tavily key still gets research
    assert build_search_adapter(real) is not None
    assert build_search_adapter(keyed(tmp_path, serper=False, tavily=False)) is None
