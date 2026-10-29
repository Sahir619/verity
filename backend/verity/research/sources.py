"""Which search results count as a source, and how they are ranked. Everything here is code, never a model.

Allowlist with tiers, matched on the parsed host name with a dot boundary (host == domain or host ends with
"." + domain), so "ieee.org.evil.com" and "notieee.org" both fail. Any other host is dropped, not shown.
  A  papers, standards and preprints
  B  primary vendor and project documents, universities
  C  open-edit or secondary pages (never enough on their own)
  P  patents.google.com, from the patents endpoint only ("has been done or claimed", never "how to build")
Score = tier weight x relevance (share of the key terms in the title plus snippet) + (11 - position) / 100.
Relevance 0 is dropped. The top `max_sources` are kept with at most `per_domain` per domain.
"""

import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

from .serper import SearchHit

TIER_A: tuple[str, ...] = (
    "ieee.org",
    "acm.org",
    "arxiv.org",
    "openreview.net",
    "usenix.org",
    "springer.com",
    "sciencedirect.com",
    "nature.com",
    "iacr.org",
)
TIER_B: tuple[str, ...] = (
    "amd.com",
    "xilinx.com",
    "intel.com",
    "ti.com",
    "analog.com",
    "microchip.com",
    "nxp.com",
    "st.com",
    "arm.com",
    "nvidia.com",
    "riscv.org",
    "chipsalliance.org",
    "lowrisc.org",
    "yosyshq.net",
    "theopenroadproject.org",
    "ethz.ch",
    "epfl.ch",
)
TIER_C: tuple[str, ...] = ("wikipedia.org", "github.com", "opencores.org")
TIER_P: tuple[str, ...] = ("patents.google.com",)

TIER_WEIGHT: dict[str, float] = {"A": 1.0, "B": 0.8, "P": 0.6, "C": 0.5}
# Suffixes only universities can register. Not a wildcard: some country zones (ac.cc, edu.pl, ...) are open to anyone.
_ACADEMIC_SUFFIXES: tuple[str, ...] = (
    "edu",
    "ac.uk",
    "edu.au",
    "ac.jp",
    "ac.kr",
    "edu.cn",
    "ac.in",
    "edu.sg",
    "edu.hk",
    "edu.tw",
    "ac.nz",
    "ac.il",
    "ac.at",
    "ac.za",
)
_ACADEMIC = re.compile(r"(^|\.)(" + "|".join(re.escape(s) for s in _ACADEMIC_SUFFIXES) + r")$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CODE_LINE = re.compile(r"^\s*(module|endmodule|always|assign|wire|reg|input|output|`|#include)\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def include_domains(endpoint: str) -> list[str]:
    """The allowlist as a domain list for a search service that filters by domain (Tavily include_domains).

    search: every allowlisted domain. scholar: papers, standards and preprints (tier A). patents: Google Patents.
    This only narrows what the service returns; tier_for still decides what counts as a source."""
    if endpoint == "patents":
        return list(TIER_P)
    if endpoint == "scholar":
        return list(TIER_A)
    return [*TIER_A, *TIER_B, *TIER_C]


def host_matches(host: str, domain: str) -> bool:
    host, domain = host.lower().rstrip("."), domain.lower()
    return host == domain or host.endswith("." + domain)


def tier_for(url: str, endpoint: str) -> str | None:
    """The tier of an http(s) URL, or None when its host is not on the allowlist (the result is dropped)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    host = parts.hostname
    if any(host_matches(host, d) for d in TIER_P):
        return "P" if endpoint == "patents" else None
    if any(host_matches(host, d) for d in TIER_A):
        return "A"
    if any(host_matches(host, d) for d in TIER_B) or _ACADEMIC.search(host):
        return "B"
    if any(host_matches(host, d) for d in TIER_C):
        return "C"
    return None


def canonical_url(url: str) -> str:
    """https, lowercase host, no fragment, no trailing slash: one id per page."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", (parts.hostname or "").lower(), path, parts.query, ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def clean_text(text: str, limit: int) -> str:
    """Untrusted web text: no control characters, no code-like lines, single spaces, capped."""
    lines = [line for line in _CONTROL.sub(" ", text or "").splitlines() if not _CODE_LINE.match(line)]
    return _SPACES.sub(" ", " ".join(lines)).strip()[:limit]


@dataclass(frozen=True, slots=True)
class Ranked:
    hit: SearchHit
    url: str
    domain: str
    tier: str
    relevance: float
    score: float


def relevance(terms: list[str], title: str, snippet: str) -> float:
    if not terms:
        return 0.0
    text = f"{title} {snippet}".lower()
    return sum(1 for t in terms if t in text) / len(terms)


def rank_hits(hits: list[SearchHit], terms: list[str], *, max_sources: int, per_domain: int, snippet_chars: int) -> list[Ranked]:
    """Allowlisted, deduplicated by canonical URL, scored in code, top `max_sources` with a per-domain cap."""
    best: dict[str, Ranked] = {}
    for hit in hits:
        tier = tier_for(hit.url, hit.endpoint)
        if tier is None:
            continue
        title = clean_text(hit.title, 200)
        snippet = clean_text(hit.snippet, snippet_chars)
        rel = relevance(terms, title, snippet)
        if rel <= 0 or not title:
            continue
        url = canonical_url(hit.url)
        score = TIER_WEIGHT[tier] * rel + (11 - min(max(hit.position, 1), 11)) / 100
        cleaned = replace(hit, title=title, snippet=snippet)
        ranked = Ranked(hit=cleaned, url=url, domain=domain_of(url), tier=tier, relevance=round(rel, 3), score=round(score, 4))
        if url not in best or best[url].score < ranked.score:
            best[url] = ranked
    ordered = sorted(best.values(), key=lambda r: (-r.score, r.url))
    out: list[Ranked] = []
    per: dict[str, int] = {}
    for r in ordered:
        if per.get(r.domain, 0) >= per_domain:
            continue
        per[r.domain] = per.get(r.domain, 0) + 1
        out.append(r)
        if len(out) >= max_sources:
            break
    return out
