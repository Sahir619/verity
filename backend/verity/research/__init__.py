"""Fact-check research (design-partner plan, claims 1.2, 1.3 and 2).

    build_search_adapter(settings)   Tavily (web) with Serper (Scholar, Patents), either one alone, the fake adapter, or
                                     None (research off); see Settings.research_adapter
    ResearchService                  /parse research and the references a new run reads (verity.research.service)
    record_run_outcome(...)          one run_outcome row plus strategy_outcome rows per run (verity.research.outcomes)
"""

from ..config import Settings
from .routed import RoutedSearchAdapter
from .serper import FakeSearchAdapter, SearchAdapter, SerperAdapter
from .service import ResearchOutcome, ResearchRequest, ResearchService, RunReferences
from .tavily import TavilyAdapter


def build_search_adapter(settings: Settings) -> SearchAdapter | None:
    """None when research is off. Each key is read from Settings (SERPER_API_KEY, TAVILY_API_KEY) and only ever sent to
    its own service. With both keys Tavily searches the web and Serper searches Scholar and Patents."""
    mode = settings.research_adapter()
    if mode == "off":
        return None
    if mode == "fake":
        return FakeSearchAdapter()
    cfg = settings.research

    def serper() -> SerperAdapter:
        key = cfg.api_key
        assert key is not None  # research_adapter() returns "serper" or "both" only with the Serper key
        return SerperAdapter(key.get_secret_value(), base_url=cfg.serper_base_url, num=cfg.num, timeout_s=cfg.timeout_s)

    def tavily() -> TavilyAdapter:
        key = cfg.tavily_api_key
        assert key is not None  # research_adapter() returns "tavily" or "both" only with the Tavily key
        return TavilyAdapter(key.get_secret_value(), base_url=cfg.tavily_base_url, num=cfg.num, timeout_s=cfg.timeout_s)

    if mode == "both":
        return RoutedSearchAdapter(tavily(), serper())
    return tavily() if mode == "tavily" else serper()


__all__ = [
    "FakeSearchAdapter",
    "ResearchOutcome",
    "ResearchRequest",
    "ResearchService",
    "RoutedSearchAdapter",
    "RunReferences",
    "SearchAdapter",
    "SerperAdapter",
    "TavilyAdapter",
    "build_search_adapter",
]
