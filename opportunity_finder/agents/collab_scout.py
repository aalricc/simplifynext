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
from shared.llm import FAST_MODEL, LLMError, available, chat_model
from shared.mcp_client import MCPError, call_tool, search_web

SYSTEM = """You are CollabScoutAgent for a creator studio manager.

You find collaboration openings: peer creators, stalls, shops, and venues that
would plausibly say yes to appearing in a video with this creator.

Rules:
- type must be "collab"
- title states who the collab is with and the format (max 12 words)
- why_now explains why this partner is reachable and why the angle is fresh
- score 0-100 on: how reachable the partner is, audience overlap, and how
  distinct the angle is from what already saturates the niche
- favour local, small, and specific partners over big accounts
- copy evidence_urls from the evidence you used; never invent URLs
- 1 to 3 opportunities"""


class CollabScoutAgent(Agent):
    name = "CollabScoutAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Find peer collab openings."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            profile = load_profile(state.get("profile"))
            niche = state.get("niche") or profile.niche
            city = state.get("city") or profile.city

            try:
                places = await call_tool("search_local_places", {"city": city})
                partners = places.get("places", [])
                web = await search_web(f"{city} {niche} creator collaboration stall", limit=4)
                hits = web.get("results", [])
            except MCPError as exc:
                return self._fallback(f"MCP unavailable ({exc})")

            if not available():
                return self._fallback("no LLM key")

            partners_block = (
                "\n".join(
                    f"- {p.get('name')} ({p.get('category')}), "
                    f"short-form: {p.get('has_short_form')}: {p.get('notes','')}"
                    for p in partners
                )
                or "(no local partners found)"
            )

            try:
                drafts = await chat_model(
                    SYSTEM,
                    f"{profile_brief(profile)}\n\n"
                    f"Local partners:\n{partners_block}\n\n"
                    f"Web evidence:\n{format_hits(hits)}",
                    DraftList,
                    model=FAST_MODEL,
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
            return {"harvested": opps, "notes": [f"{self.name}: {len(opps)} collabs"]}

    def _fallback(self, why: str) -> dict[str, Any]:
        seeds = seed_opportunities(source_agent=self.name)
        return {"harvested": seeds, "notes": [f"{self.name}: fallback to seed ({why})"]}
