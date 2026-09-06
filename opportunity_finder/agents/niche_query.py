from typing import Any

from observability.otel import agent_span
from opportunity_finder.agents.state import QueryList, load_profile, profile_brief
from shared.agent_base import Agent
from shared.llm import FAST_MODEL, LLMError, available, chat_model

SYSTEM = """You are NicheQueryAgent for a creator studio manager.

You turn a creator profile into web search queries that will surface real,
actionable content opportunities: rising hooks, local brands with weak
short-form presence, and peer creators open to collaboration.

Rules:
- 5 to 10 queries, each 3-9 words
- Ground them in the creator's city and niche, not generic advice
- Cover all three angles: trends, local brands, collaborations
- Never produce queries about the creator's no-go topics
- Return search queries, not questions or sentences"""


class NicheQueryAgent(Agent):
    name = "NicheQueryAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Turn creator profile into search queries."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            profile = load_profile(state.get("profile"))
            niche = state.get("niche") or profile.niche
            city = state.get("city") or profile.city

            if available():
                try:
                    result = await chat_model(
                        SYSTEM,
                        f"{profile_brief(profile)}\n\n"
                        f"Generate search queries for niche '{niche}' in {city}.",
                        QueryList,
                        model=FAST_MODEL,
                        agent=self.name,
                    )
                    queries = [q.strip() for q in result.queries if q.strip()][:10]
                    if queries:
                        return {"queries": queries, "notes": [f"{self.name}: {len(queries)} queries"]}
                except LLMError as exc:
                    return {
                        "queries": self._fallback(niche, city),
                        "notes": [f"{self.name}: llm failed ({exc}); used fallback queries"],
                    }

            return {
                "queries": self._fallback(niche, city),
                "notes": [f"{self.name}: no LLM, used fallback queries"],
            }

    @staticmethod
    def _fallback(niche: str, city: str) -> list[str]:
        return [
            f"{niche} trending recipes {city}",
            f"quick weeknight {niche} ideas",
            f"{city} food brands weak social media",
            f"local {niche} brands short form video gap",
            f"{city} hawker stall collaboration creators",
            f"{niche} audience preferences {city}",
        ]
