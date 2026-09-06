from typing import Any

from observability.otel import agent_span
from opportunity_finder.agents.state import ScoreList, load_profile, profile_brief
from shared.agent_base import Agent
from shared.llm import LLMError, available, chat_model

SYSTEM = """You are OpportunityScorerAgent for a creator studio manager.

You do the final ranking. Every opportunity already has a provisional score from
the scout that found it; those scouts each saw only their own slice. You see all
of them together and re-score for true fit.

Score 0-100 on:
- Audience fit: would THIS creator's audience watch and save it
- Goal fit: does it move the creator toward their stated goals
- Effort: can it be produced within the creator's weekly cadence
- Evidence: is the why_now backed by real evidence or is it speculation
- Memory: if past performance is provided, weight proven winners UP and
  known losers DOWN. This is how the studio adapts week over week.

Rules:
- Score every id you are given, exactly once
- Use the full range; do not cluster everything at 70-85
- reason is one short clause naming the deciding factor"""


class OpportunityScorerAgent(Agent):
    name = "OpportunityScorerAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Rank opportunities 0-100 for profile fit."""
        with agent_span(self.name, self.kind, state.get("run_id", "")):
            profile = load_profile(state.get("profile"))
            items: list[dict[str, Any]] = state.get("clustered", []) or state.get("harvested", [])
            limit = int(state.get("limit", 8))
            memory = state.get("memory") or {}

            if not items:
                return {"opportunities": [], "notes": [f"{self.name}: nothing to score"]}

            if not available():
                ranked = sorted(items, key=lambda o: o.get("score", 0), reverse=True)[:limit]
                return {
                    "opportunities": ranked,
                    "notes": [f"{self.name}: no LLM, kept scout scores"],
                }

            listing = "\n".join(
                f"- id={o['id']} [{o['type']}] {o['title']}\n"
                f"  why_now: {o.get('why_now','')}\n"
                f"  scout score: {o.get('score', 0)} (from {o.get('source_agent','?')})"
                for o in items
            )

            memory_block = ""
            if any(memory.get(k) for k in ("wins", "losses", "next_bias")):
                memory_block = (
                    "\n\nPast performance memory:\n"
                    f"  wins: {', '.join(memory.get('wins', [])) or 'none yet'}\n"
                    f"  losses: {', '.join(memory.get('losses', [])) or 'none yet'}\n"
                    f"  bias next week toward: {', '.join(memory.get('next_bias', [])) or 'n/a'}"
                )

            try:
                result = await chat_model(
                    SYSTEM,
                    f"{profile_brief(profile)}{memory_block}\n\nOpportunities:\n{listing}",
                    ScoreList,
                    agent=self.name,
                )
            except LLMError as exc:
                ranked = sorted(items, key=lambda o: o.get("score", 0), reverse=True)[:limit]
                return {
                    "opportunities": ranked,
                    "notes": [f"{self.name}: llm failed ({exc}); kept scout scores"],
                }

            scores = {s.id: s for s in result.scored}
            rescored: list[dict[str, Any]] = []
            for opp in items:
                out = dict(opp)
                hit = scores.get(opp["id"])
                if hit:
                    out["score"] = hit.score
                    if hit.reason:
                        note = f"[{self.name}] {hit.reason}"
                        out["raw_notes"] = f"{out.get('raw_notes','')} {note}".strip()
                rescored.append(out)

            rescored.sort(key=lambda o: o.get("score", 0), reverse=True)
            top = rescored[:limit]

            return {
                "opportunities": top,
                "notes": [
                    f"{self.name}: scored {len(rescored)}, returned top {len(top)}"
                    + (" with memory bias" if memory_block else "")
                ],
            }
