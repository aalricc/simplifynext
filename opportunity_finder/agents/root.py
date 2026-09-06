from typing import Any
from uuid import uuid4

from observability.otel import agent_span
from opportunity_finder.agents.pipeline import OpportunityFinderPipeline
from shared.agent_base import Agent
from shared.http_clients import get_memory

PIPELINE = OpportunityFinderPipeline()


class OpportunityFinderRoot(Agent):
    name = "OpportunityFinderRoot"
    kind = "custom"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """FastAPI entry that runs the finder pipeline."""
        run_id = state.get("run_id") or f"finder-{uuid4().hex[:8]}"
        limit = int(state.get("limit", 8))

        with agent_span(self.name, self.kind, run_id):
            # Week-2 adapt: past performance biases this week's ranking. The
            # Pipeline Manager may be down or unimplemented -- that is fine.
            memory = state.get("memory")
            if memory is None:
                try:
                    memory = await get_memory()
                except Exception:  # noqa: BLE001 - never block the finder on P3
                    memory = {}

            try:
                result = await PIPELINE.run({**state, "run_id": run_id, "memory": memory})
            except Exception as exc:  # noqa: BLE001 - report, never fabricate
                # This used to serve `demo/maya/opportunities_seed.json`. With
                # real accounts that is one persona's opportunities handed to
                # someone else and labelled a result -- an empty list plus a
                # visible note is the honest answer.
                return {
                    "run_id": run_id,
                    "opportunities": [],
                    "queries": [],
                    "notes": [f"{self.name}: pipeline failed ({exc})"],
                    "mode": "failed",
                    "error": str(exc),
                }

            opportunities = result.get("opportunities") or []
            return {
                "run_id": run_id,
                "opportunities": opportunities[:limit],
                "queries": result.get("queries", []),
                "notes": result.get("notes", []),
                "mode": "live" if opportunities else "empty",
            }
