from typing import Any

from pydantic import BaseModel, Field

from observability.otel import agent_span
from shared.agent_base import Agent
from shared.llm import FAST_MODEL, LLMError, available, chat_model

SYSTEM = """You are OpportunityClusterAgent for a creator studio manager.

Three scout agents ran in parallel and their findings overlap. You merge
near-duplicates so the creator sees distinct choices, not the same idea three
times.

Rules:
- Two items are duplicates when they would produce essentially the same video
- Merge duplicates: keep the clearest title, keep the HIGHEST score, and union
  their evidence_urls
- Different angles on the same subject are NOT duplicates (a brand partnership
  with a laksa paste maker is distinct from a laksa recipe trend)
- Never invent new opportunities and never drop a unique one
- Return the id of every opportunity to keep, with the ids it absorbed"""


class ClusterDecision(BaseModel):
    keep_id: str
    absorbed_ids: list[str] = Field(default_factory=list)
    title: str = ""


class ClusterResult(BaseModel):
    clusters: list[ClusterDecision] = Field(default_factory=list)


class OpportunityClusterAgent(Agent):
    name = "OpportunityClusterAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Cluster similar finds."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            harvested: list[dict[str, Any]] = state.get("harvested", [])

            # Exact-id dedupe first -- cheap, and parallel agents hitting the
            # same seed fallback produce literal duplicates.
            by_id: dict[str, dict[str, Any]] = {}
            for opp in harvested:
                existing = by_id.get(opp["id"])
                if existing is None or opp.get("score", 0) > existing.get("score", 0):
                    by_id[opp["id"]] = opp
            unique = list(by_id.values())

            if len(unique) < 2 or not available():
                return {
                    "clustered": unique,
                    "notes": [f"{self.name}: {len(unique)} after id dedupe"],
                }

            listing = "\n".join(
                f"- {o['id']} [{o['type']}] {o['title']} (score {o.get('score', 0)})"
                for o in unique
            )

            try:
                result = await chat_model(
                    SYSTEM,
                    f"Opportunities:\n{listing}",
                    ClusterResult,
                    model=FAST_MODEL,
                    agent=self.name,
                )
            except LLMError as exc:
                return {
                    "clustered": unique,
                    "notes": [f"{self.name}: llm failed ({exc}); kept {len(unique)} unique"],
                }

            merged: list[dict[str, Any]] = []
            seen: set[str] = set()
            for decision in result.clusters:
                keep = by_id.get(decision.keep_id)
                if keep is None or decision.keep_id in seen:
                    continue
                seen.add(decision.keep_id)

                keep = dict(keep)
                urls = list(keep.get("evidence_urls", []))
                for absorbed_id in decision.absorbed_ids:
                    other = by_id.get(absorbed_id)
                    if not other:
                        continue
                    seen.add(absorbed_id)
                    keep["score"] = max(keep.get("score", 0), other.get("score", 0))
                    urls += [u for u in other.get("evidence_urls", []) if u not in urls]
                keep["evidence_urls"] = urls
                merged.append(keep)

            # Safety net: never let the model silently drop an opportunity.
            merged += [o for o in unique if o["id"] not in seen]

            return {
                "clustered": merged,
                "notes": [f"{self.name}: {len(unique)} -> {len(merged)} after clustering"],
            }
