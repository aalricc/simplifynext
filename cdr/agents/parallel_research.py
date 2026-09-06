import asyncio
from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.agents.audience_research import AudienceResearchAgent
from cdr.agents.pain_point import PainPointAgent
from cdr.agents.peer_creator_analysis import PeerCreatorAnalysisAgent
from cdr.agents.platform_presence import PlatformPresenceAgent
from shared.agent_base import Agent
from shared.schemas import ResearchBrief


class ParallelResearch(Agent):
    name = "ParallelResearch"
    kind = "parallel"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "parallel", "Fan-out: audience, peers, presence, pain.")
            specialists = [
                AudienceResearchAgent(),
                PeerCreatorAnalysisAgent(),
                PlatformPresenceAgent(),
                PainPointAgent(),
            ]
            await asyncio.gather(*[a.run(state) for a in specialists])
            opp = state.get("current") or {}
            brief = ResearchBrief(
                opportunity_id=str(opp.get("id", "")),
                audience_insight=str(state.get("audience_insight", "")),
                peer_moves=str(state.get("peer_moves", "")),
                platform_presence=str(state.get("platform_presence", "")),
                pain_points=str(state.get("pain_points", "")),
                evidence_urls=list(opp.get("evidence_urls") or []),
                risks=["unsourced health claims"],
            )
            state["brief"] = brief.model_dump()
            artifact(state, "render_research_brief",
                     agui_map.research_brief_args(state["brief"], opp))
            ping(state, self.name, "parallel", "Gather complete.", artifact_ref="ResearchBrief")
        return state
