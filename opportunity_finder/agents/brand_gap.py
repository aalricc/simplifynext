from typing import Any

from observability.otel import agent_span
from opportunity_finder.agents.state import (
    DraftList,
    draft_to_opportunity,
    format_hits,
    load_profile,
    profile_brief,
    seed_opportunities,
)
from shared.agent_base import Agent
from shared.llm import LLMError, available, chat_model
from shared.mcp_client import MCPError, call_tool, search_web

SYSTEM = """You are BrandGapAgent for a creator studio manager.

You find LOCAL BRANDS with a strong product and weak or missing short-form
content. These are the creator's best paid-partnership targets: the brand needs
what the creator makes.

Rules:
- type must be "brand"
- title names the brand and the gap (max 12 words)
- why_now states the evidence of the content gap, in one sentence
- score 0-100 on partnership fit: product relevance to the niche, size of the
  content gap, and whether the creator's audience would plausibly buy
- prefer brands with a real storefront or retail presence over pure online shops
- copy evidence_urls from the evidence you used; never invent URLs
- 1 to 3 opportunities"""


class BrandGapAgent(Agent):
    name = "BrandGapAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Find local brands with weak or missing content."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            profile = load_profile(state.get("profile"))
            niche = state.get("niche") or profile.niche
            city = state.get("city") or profile.city

            try:
                # The gap IS the filter: brands without short-form presence.
                places = await call_tool(
                    "search_local_places", {"city": city, "has_short_form": False}
                )
                gaps = places.get("places", [])
                web = await search_web(f"{city} {niche} local brands weak social media", limit=4)
                hits = web.get("results", [])
            except MCPError as exc:
                return self._fallback(f"MCP unavailable ({exc})")

            if not available():
                return self._fallback("no LLM key")

            places_block = (
                "\n".join(
                    f"- {p.get('name')} ({p.get('category')}): {p.get('notes','')}" for p in gaps
                )
                or "(no local places with a content gap)"
            )

            try:
                drafts = await chat_model(
                    SYSTEM,
                    f"{profile_brief(profile)}\n\n"
                    f"Local brands with NO short-form content:\n{places_block}\n\n"
                    f"Supporting web evidence:\n{format_hits(hits)}",
                    DraftList,
                    agent=self.name,
                )
            except LLMError as exc:
                return self._fallback(f"llm failed ({exc})")

            opps = [
                draft_to_opportunity(d, city=city, niche=niche, source_agent=self.name).model_dump(
                    mode="json"
                )
                for d in drafts.opportunities
            ]
            return {"harvested": opps, "notes": [f"{self.name}: {len(opps)} brand gaps"]}

    def _fallback(self, why: str) -> dict[str, Any]:
        seeds = seed_opportunities(source_agent=self.name)
        return {"harvested": seeds, "notes": [f"{self.name}: fallback to seed ({why})"]}
