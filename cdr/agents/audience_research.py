from typing import Any

from cdr.agents._util import ping, with_span
from cdr.llm import complete_json
from cdr.mcp_client import retrieve_creator_memory
from shared.agent_base import Agent


class AudienceResearchAgent(Agent):
    name = "AudienceResearchAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "llm", "Audience insight for current opportunity.")
            # Retrieve against this creator's own niche and the opportunity in
            # hand. The query used to be the literal string "maya audience
            # laksa hostel", which scored every other creator's corpus at zero.
            profile = state.get("profile") or {}
            current = state.get("current") or {}
            query = " ".join(
                str(part)
                for part in (
                    profile.get("niche", ""),
                    profile.get("city", ""),
                    current.get("title", ""),
                    "audience",
                )
                if part
            )
            mem = await retrieve_creator_memory(query)
            data = await complete_json(
                "You are AudienceResearchAgent. Return JSON {audience_insight}.",
                f"opp={state.get('current')}\nmemory={mem}",
                agent=self.name,
            )
            state["audience_insight"] = data.get("audience_insight", "")
            ping(state, self.name, "tool", "retrieve_creator_memory used", artifact_ref="rag")
        return state
