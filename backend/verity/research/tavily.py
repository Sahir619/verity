"""Tavily search adapter: POST https://api.tavily.com/search with `Authorization: Bearer <key>` (docs.tavily.com,
checked 2026-10-22). General web search for the fact-check; Serper keeps Google Scholar and Patents when both keys exist.

The key goes only in the Authorization header, only to research.tavily_base_url (the production guard pins its host),
and is never logged: request headers are not logged anywhere, verity.api.redact scrubs Authorization headers and
`tvly-` keys, and error messages are built here from the status code only. Queries come from code templates
(verity.research.queries), never raw user text.

What is sent: query, search_depth "basic" (1 credit), max_results, include_domains (the tier allowlist from
verity.research.sources, which only narrows the results: code still drops any host not on the allowlist), and
include_usage so the response says what the call cost. Tavily's own `answer` is never requested: the summary is ours,
written by the research route and checked by code against numbered sources.

Used alone (no Serper key) it also serves the scholar and patents queries, restricted to the domains of those
endpoints (papers and standards, Google Patents). The hit keeps the endpoint of the query that asked for it.
"""

import asyncio
import math
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .serper import Endpoint, SearchError, SearchHit, SearchResult
from .sources import include_domains

SEARCH_DEPTH = "basic"  # 1 credit per call (advanced is 2)
_CREDITS_FOR_DEPTH = {"basic": 1, "fast": 1, "ultra-fast": 1, "advanced": 2}


def _date(raw: Any) -> str | None:
    """published_date arrives as an RFC 822 date ("Tue, 11 Mar 2025 17:00:00 GMT"); show it as YYYY-MM-DD."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return raw[:32]


def parse_results(endpoint: Endpoint, query_idx: int, data: Any, limit: int = 10) -> list[SearchHit]:
    """Tavily's `results` list: title, url, content (the snippet), score, published_date (when asked for)."""
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    hits: list[SearchHit] = []
    for i, item in enumerate(items[:limit]):
        if not isinstance(item, dict):
            continue
        title, url = item.get("title"), item.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        content = item.get("content")
        hits.append(
            SearchHit(
                endpoint=endpoint,
                query_idx=query_idx,
                title=title,
                url=url,
                snippet=content if isinstance(content, str) else "",
                date=_date(item.get("published_date")),
                position=i + 1,  # Tavily returns the results ranked and gives no position field
            )
        )
    return hits


def credits_used(data: Any) -> int:
    """`usage.credits` of the response (asked for with include_usage); a basic search is 1 credit when it is absent."""
    usage = data.get("usage") if isinstance(data, dict) else None
    credits = usage.get("credits") if isinstance(usage, dict) else None
    if isinstance(credits, (int, float)) and not isinstance(credits, bool) and math.isfinite(credits) and credits >= 0:
        return math.ceil(credits)
    return _CREDITS_FOR_DEPTH[SEARCH_DEPTH]


class TavilyAdapter:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        num: int,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
        domains: Callable[[str], list[str]] = include_domains,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._num = max(1, min(int(num), 20))
        self._timeout = timeout_s
        self._client = client
        self._domains = domains

    def __repr__(self) -> str:  # never print the key
        return f"TavilyAdapter(base_url={self._base!r})"

    def provider_for(self, endpoint: Endpoint) -> str:
        return "tavily"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, endpoint: Endpoint, query: str, query_idx: int) -> SearchResult:
        body: dict[str, Any] = {
            "query": query,
            "search_depth": SEARCH_DEPTH,
            "max_results": self._num,
            "include_domains": self._domains(endpoint),
            "include_published_date": True,
            "include_usage": True,
        }
        try:
            res = await asyncio.wait_for(
                self._get_client().post(
                    f"{self._base}/search",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                ),
                timeout=self._timeout + 2,
            )
        except (TimeoutError, httpx.TimeoutException):
            raise SearchError("timeout", f"search timed out after {self._timeout:.0f} s") from None
        except httpx.HTTPError as exc:
            raise SearchError("network", f"could not reach the search service ({type(exc).__name__})") from None
        status = res.status_code
        if status in (401, 403):
            raise SearchError("auth", f"the search service refused the key (HTTP {status})")
        if status == 429:
            raise SearchError("rate_limited", "the search service is rate limiting (HTTP 429)")
        if status in (432, 433):
            raise SearchError("plan_limit", f"the search service says this key's credit limit is used up (HTTP {status})")
        if status >= 500:
            raise SearchError("server", f"the search service failed (HTTP {status})")
        if status != 200:
            raise SearchError("bad_request", f"the search service answered HTTP {status}")
        try:
            data = res.json()
        except ValueError:
            raise SearchError("bad_response", "the search service answered with something that is not JSON") from None
        return SearchResult(hits=parse_results(endpoint, query_idx, data), credits=credits_used(data))
