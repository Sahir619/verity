"""Both search keys at once: Tavily searches the web, Serper searches Google Scholar and Google Patents.

Each query still goes to exactly one service, so one research costs the same number of calls as with one key; only the
service behind each endpoint changes.
"""

from .serper import Endpoint, SearchAdapter, SearchResult


class RoutedSearchAdapter:
    def __init__(self, web: SearchAdapter, scholar_and_patents: SearchAdapter) -> None:
        self._web = web
        self._other = scholar_and_patents
        self.name = f"{web.name}+{scholar_and_patents.name}"

    def __repr__(self) -> str:
        return f"RoutedSearchAdapter({self._web!r}, {self._other!r})"

    def _for(self, endpoint: Endpoint) -> SearchAdapter:
        return self._web if endpoint == "search" else self._other

    def provider_for(self, endpoint: Endpoint) -> str:
        return self._for(endpoint).provider_for(endpoint)

    async def search(self, endpoint: Endpoint, query: str, query_idx: int) -> SearchResult:
        return await self._for(endpoint).search(endpoint, query, query_idx)

    async def aclose(self) -> None:
        await self._web.aclose()
        await self._other.aclose()
