"""Search adapters: Serper (POST https://google.serper.dev/{search|scholar|patents}) and a fake for tests and for
runs without a key. The Tavily adapter is in verity.research.tavily; verity.research.routed sends web queries to
Tavily and Scholar and Patents queries to Serper when both keys exist.

The key goes only in the X-API-KEY header, only to research.serper_base_url (the production guard pins its host), and
is never logged: request headers are not logged anywhere, verity.api.redact scrubs X-API-KEY, and error messages are
built here from the status code only. Queries come from code templates (verity.research.queries), never raw user text.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

Endpoint = Literal["search", "scholar", "patents"]
ENDPOINTS: tuple[Endpoint, ...] = ("search", "scholar", "patents")


@dataclass(frozen=True, slots=True)
class SearchHit:
    endpoint: Endpoint
    query_idx: int
    title: str
    url: str
    snippet: str
    date: str | None
    position: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[SearchHit]
    credits: int  # what this call cost (Serper bills 1 credit per search, scholar or patents call)


class SearchError(Exception):
    """One search call failed. `kind`: auth | rate_limited | plan_limit | server | timeout | bad_request | bad_response |
    network. plan_limit: the service says this key's plan or pay-as-you-go limit is used up (Tavily HTTP 432 and 433)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class SearchAdapter(Protocol):
    name: str

    def provider_for(self, endpoint: Endpoint) -> str:
        """Who bills a call to this endpoint: "serper", "tavily", or the adapter's own name (the fake one, no credits)."""
        ...

    async def search(self, endpoint: Endpoint, query: str, query_idx: int) -> SearchResult: ...

    async def aclose(self) -> None: ...


def _date(item: dict[str, Any]) -> str | None:
    for key in ("date", "publicationDate", "priorityDate", "filingDate", "year"):
        value = item.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()[:32]
    return None


def parse_organic(endpoint: Endpoint, query_idx: int, data: Any) -> list[SearchHit]:
    """Serper's `organic` list (same field names for search, scholar and patents: title, link, snippet)."""
    items = data.get("organic") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    hits: list[SearchHit] = []
    for i, item in enumerate(items[:10]):
        if not isinstance(item, dict):
            continue
        title, link = item.get("title"), item.get("link")
        if not isinstance(title, str) or not isinstance(link, str):
            continue
        snippet = item.get("snippet")
        if not isinstance(snippet, str):
            snippet = item.get("publicationInfo") if isinstance(item.get("publicationInfo"), str) else ""
        position = item.get("position")
        hits.append(
            SearchHit(
                endpoint=endpoint,
                query_idx=query_idx,
                title=title,
                url=link,
                snippet=snippet or "",
                date=_date(item),
                position=position if isinstance(position, int) and not isinstance(position, bool) else i + 1,
            )
        )
    return hits


class SerperAdapter:
    name = "serper"

    def __init__(self, api_key: str, *, base_url: str, num: int, timeout_s: float, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._num = max(1, min(int(num), 10))
        self._timeout = timeout_s
        self._client = client

    def __repr__(self) -> str:  # never print the key
        return f"SerperAdapter(base_url={self._base!r})"

    def provider_for(self, endpoint: Endpoint) -> str:
        return "serper"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, endpoint: Endpoint, query: str, query_idx: int) -> SearchResult:
        if endpoint not in ENDPOINTS:
            raise SearchError("bad_request", f"unknown search endpoint {endpoint!r}")
        try:
            res = await asyncio.wait_for(
                self._get_client().post(
                    f"{self._base}/{endpoint}",
                    json={"q": query, "num": self._num},
                    headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
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
        if status >= 500:
            raise SearchError("server", f"the search service failed (HTTP {status})")
        if status != 200:
            raise SearchError("bad_request", f"the search service answered HTTP {status}")
        try:
            data = res.json()
        except ValueError:
            raise SearchError("bad_response", "the search service answered with something that is not JSON") from None
        return SearchResult(hits=parse_organic(endpoint, query_idx, data), credits=1)


class FakeSearchAdapter:
    """Deterministic results, no network, no credits. Used by tests and whenever no SERPER_API_KEY is set.

    Every query gets allowlisted results whose titles and snippets carry the query words, so research, scoring,
    citation checks and technique checks all run. Each query also returns one off-list host and one look-alike host
    ("ieee.org.example.com"), which the allowlist must drop. A query containing "verity-fail" fails like Serper's 5xx.
    """

    name = "fake"

    _FAMILY_TEXT: dict[str, str] = {
        "adder": "Ripple carry, carry lookahead and carry select adders compared for area and delay; Kogge-Stone and "
        "Brent-Kung prefix adders trade cells for logic levels.",
        "counter": "A synchronous binary counter built from a ripple incrementer or a lookahead incrementer that splits "
        "the carry chain.",
        "multiply": "Multiply accumulate units with a radix-4 Booth multiplier, a Wallace tree or a Dadda tree and a "
        "carry-save accumulator.",
        "sequence": "A sequence detector as a finite state machine with binary or one-hot state encoding, or a shift "
        "register that matches the pattern.",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def aclose(self) -> None:
        return None

    def provider_for(self, endpoint: Endpoint) -> str:
        return self.name

    async def search(self, endpoint: Endpoint, query: str, query_idx: int) -> SearchResult:
        self.calls.append((endpoint, query))
        if "verity-fail" in query:
            raise SearchError("server", "the search service failed (HTTP 503)")
        low = query.lower()
        extra = next((text for word, text in self._FAMILY_TEXT.items() if word in low), "")
        slug = "-".join(low.split())[:60]
        snippet = f"Survey of {low} designs. {extra}".strip()
        if endpoint == "patents":
            rows = [("patents.google.com", f"/patent/US{1000000 + len(slug)}A", f"Circuit for {low}", None)]
        elif endpoint == "scholar":
            rows = [
                ("ieeexplore.ieee.org", f"/document/{900 + len(slug)}", f"A study of {low}", "2023"),
                ("arxiv.org", f"/abs/2401.{1000 + len(slug)}", f"On {low}", "2024"),
            ]
        else:
            rows = [
                ("en.wikipedia.org", f"/wiki/{slug}", f"{query.title()} - Wikipedia", None),
                ("www.cs.example.edu", f"/notes/{slug}", f"Lecture notes: {low}", "2022"),
            ]
        rows += [
            ("blog.example.com", f"/{slug}", f"My blog about {low}", None),
            ("ieee.org.example.com", f"/{slug}", f"Look-alike page about {low}", None),
        ]
        hits = [
            SearchHit(
                endpoint=endpoint,
                query_idx=query_idx,
                title=title,
                url=f"https://{host}{path}",
                snippet=snippet,
                date=date,
                position=i + 1,
            )
            for i, (host, path, title, date) in enumerate(rows)
        ]
        return SearchResult(hits=hits, credits=0)
