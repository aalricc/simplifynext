"""P1 search tools for the MCP server.

Every tool returns the SAME shape whether it ran on fixtures or live, so
flipping USE_FIXTURES=0 on day 5 changes no downstream agent code.

  search_web           -> {"results": [{title, url, snippet, published, source}]}
  search_local_places  -> {"places":  [{place_id, name, city, category, ...}]}
  fetch_url            -> {"url", "status", "title", "text", "fetched"}
  find_opportunities   -> {"opportunities": [...], "run_id", "mode", ...}
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from mcp_server.tools import tool
from shared.fixtures import persona_dir, persona_file
from shared.flags import use_fixtures
from shared.tenant import current_profile

# Resolved per demo persona, not pinned to Maya -- see shared/fixtures.py.
def _search_fixture() -> Path:
    return persona_file("search_web.json")


def _places_fixture() -> Path:
    # Maya's file predates the per-persona layout and keeps its original name.
    path = persona_dir() / "places.json"
    return path if path.exists() else persona_file("places_sg_food.json")

FETCH_TIMEOUT = 10.0
FETCH_MAX_BYTES = 500_000
# Seed evidence_urls use this fake TLD; it never resolves. Serve fixtures for it
# so P2's FactChecker demo beat works offline.
FIXTURE_HOSTS = ("example.local", "example.com")


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


# --------------------------------------------------------------------------
# search_web
# --------------------------------------------------------------------------


def _live_search_enabled() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip()) and not use_fixtures()


async def _search_web_live(query: str, limit: int) -> list[dict[str, Any]]:
    """Tavily adapter. Only reached when TAVILY_API_KEY is set and fixtures off."""
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            "https://api.tavily.com/search",
            json={
                "api_key": os.getenv("TAVILY_API_KEY"),
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
        )
        r.raise_for_status()
        payload = r.json()

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "published": item.get("published_date", ""),
            "source": "tavily",
        }
        for item in payload.get("results", [])[:limit]
    ]


def _search_web_fixture(query: str, limit: int) -> list[dict[str, Any]]:
    """Keyword-overlap ranking over the demo corpus."""
    corpus = _load(_search_fixture(), {}).get("results", [])
    q = _tokens(query)

    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in corpus:
        haystack = _tokens(doc.get("title", "") + " " + doc.get("snippet", ""))
        haystack |= {t for kw in doc.get("keywords", []) for t in _tokens(kw)}
        # keyword hits weigh double -- they are the curated demo signal
        score = len(q & haystack) + len(q & {t for kw in doc.get("keywords", []) for t in _tokens(kw)})
        if score:
            scored.append((score, doc))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    hits = [doc for _, doc in scored[:limit]]
    if not hits:  # never return empty -- agents must always have something to reason over
        hits = corpus[:limit]

    return [{k: v for k, v in doc.items() if k != "keywords"} for doc in hits]


@tool("search_web", owner="P1", description="Search the web for trends, brands, and peer creators.")
async def search_web(query: str, limit: int = 5) -> dict[str, Any]:
    """Ranked web results for a query. Falls back to fixtures on any live failure."""
    if _live_search_enabled():
        try:
            results = await _search_web_live(query, limit)
            if results:
                return {"query": query, "results": results, "mode": "live"}
        except Exception as exc:  # noqa: BLE001 - demo must never hard-fail
            return {
                "query": query,
                "results": _search_web_fixture(query, limit),
                "mode": "fixture",
                "warning": f"live search failed, used fixtures: {exc}",
            }

    return {"query": query, "results": _search_web_fixture(query, limit), "mode": "fixture"}


# --------------------------------------------------------------------------
# search_local_places
# --------------------------------------------------------------------------


@tool(
    "search_local_places",
    owner="P1",
    description="Find local businesses; filter by city, category, or missing short-form content.",
)
async def search_local_places(
    city: str = "",
    category: str = "",
    has_short_form: bool | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Local places for brand-gap and collab scouting.

    `has_short_form=False` is the BrandGapAgent path: brands with a real product
    and no short-form presence.
    """
    places: list[dict[str, Any]] = _load(_places_fixture(), [])

    if city:
        places = [p for p in places if p.get("city", "").lower() == city.lower()]
    if category:
        places = [p for p in places if p.get("category", "").lower() == category.lower()]
    if has_short_form is not None:
        places = [p for p in places if bool(p.get("has_short_form")) is bool(has_short_form)]

    return {
        "city": city,
        "places": places[:limit],
        "count": len(places[:limit]),
        "mode": "fixture",
    }


# --------------------------------------------------------------------------
# fetch_url
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)


def _html_to_text(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_from_fixture(url: str) -> dict[str, Any] | None:
    """Serve seed evidence_urls (example.local) out of the search corpus."""
    for doc in _load(_search_fixture(), {}).get("results", []):
        if doc.get("url") == url:
            return {
                "url": url,
                "status": 200,
                "title": doc.get("title", ""),
                "text": doc.get("snippet", ""),
                "published": doc.get("published", ""),
                "fetched": "fixture",
            }
    return None


@tool("fetch_url", owner="P1", description="Fetch a URL and return readable text for fact-checking.")
async def fetch_url(url: str, max_chars: int = 4000) -> dict[str, Any]:
    """Fetch and flatten a page. Unresolvable demo URLs resolve from fixtures."""
    if not url.startswith(("http://", "https://")):
        return {"url": url, "status": 0, "title": "", "text": "", "fetched": "error",
                "error": "url must start with http:// or https://"}

    host = httpx.URL(url).host or ""
    if any(host.endswith(h) for h in FIXTURE_HOSTS) or use_fixtures():
        hit = _fetch_from_fixture(url)
        if hit:
            return hit
        if any(host.endswith(h) for h in FIXTURE_HOSTS):
            return {"url": url, "status": 404, "title": "", "text": "", "fetched": "fixture",
                    "error": "no fixture for this demo URL"}

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "CreatorLoop/0.1 (hackathon demo)"})
            body = r.text[:FETCH_MAX_BYTES]
    except httpx.HTTPError as exc:
        return {"url": url, "status": 0, "title": "", "text": "", "fetched": "error",
                "error": f"{type(exc).__name__}: {exc}"}

    title_match = _TITLE_RE.search(body)
    return {
        "url": url,
        "status": r.status_code,
        "title": _html_to_text(title_match.group(1)) if title_match else "",
        "text": _html_to_text(body)[:max_chars],
        "fetched": "live",
    }


# --------------------------------------------------------------------------
# find_opportunities
# --------------------------------------------------------------------------


@tool(
    "find_opportunities",
    owner="P1",
    description="Run the Opportunity Finder pipeline and return ranked opportunities.",
)
async def find_opportunities(
    niche: str = "",
    city: str = "",
    limit: int = 8,
    profile_id: str = "",
    profile: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Agent-as-tool entry used by CDR when it calls MCP instead of :8081.

    No persona defaults: searching Singapore hawker food for a creator who
    never said either is a confident wrong answer.
    """
    from opportunity_finder.graph import run_search

    profile_id = profile_id or (profile or {}).get("id") or current_profile()
    if not profile_id:
        return {"opportunities": [], "error": "find_opportunities requires a creator profile"}

    state = await run_search(
        {
            "run_id": kwargs.get("run_id") or "mcp-find",
            "profile_id": profile_id,
            "profile": profile or {"id": profile_id, "niche": niche, "city": city},
            "niche": niche,
            "city": city,
            "limit": int(limit),
        }
    )
    opps = list(state.get("opportunities") or [])
    return {
        "opportunities": opps[: int(limit)],
        "run_id": state.get("run_id"),
        "mode": state.get("mode", "live"),
        "notes": state.get("notes", []),
    }
