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
from shared.mcp_client import MCPError, search_web

SYSTEM = """You are TrendHarvesterAgent for a creator studio manager.

From web search results, extract content opportunities the creator could film
this week: rising hooks, formats, and angles with real momentum.

Rules:
- type must be "trend"
- title is the content hook itself, filmable as one post (max 12 words)
- why_now explains the timing signal from the evidence, in one sentence
- score 0-100 on fit for THIS creator's audience and cadence
- copy evidence_urls from the results you used; never invent URLs
- respect the creator's no-go topics
- 2 to 4 opportunities, no duplicates"""


class TrendHarvesterAgent(Agent):
    name = "TrendHarvesterAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Find trending topics and hooks."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            profile = load_profile(state.get("profile"))
            niche = state.get("niche") or profile.niche
            city = state.get("city") or profile.city
            queries = state.get("queries") or [f"{niche} trends {city}"]

            hits: list[dict[str, Any]] = []
            try:
                for q in queries[:3]:
                    res = await search_web(q, limit=4)
                    hits.extend(res.get("results", []))
            except MCPError as exc:
                return self._fallback(f"MCP unavailable ({exc})")

            if not available():
                return self._fallback("no LLM key")

            try:
                drafts = await chat_model(
                    SYSTEM,
                    f"{profile_brief(profile)}\n\nSearch results:\n{format_hits(hits)}",
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
            return {"harvested": opps, "notes": [f"{self.name}: {len(opps)} trends"]}

    def _fallback(self, why: str) -> dict[str, Any]:
        seeds = seed_opportunities(source_agent=self.name)
        return {"harvested": seeds, "notes": [f"{self.name}: fallback to seed ({why})"]}
