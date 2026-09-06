from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.agents.draft_writer import DraftWriterAgent
from cdr.agents.fact_checker import FactCheckerAgent
from cdr.agents.voice_critique import VoiceCritiqueAgent
from shared.agent_base import Agent


class RefinementLoop(Agent):
    name = "RefinementLoop"
    kind = "loop"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "loop", "QA until pass or max 3 iterations.")
            fact = FactCheckerAgent()
            voice = VoiceCritiqueAgent()
            writer = DraftWriterAgent()
            for i in range(1, 4):
                state["iteration"] = i
                await fact.run(state)
                await voice.run(state)
                # One card per round, both critics on it - that is the shape
                # render_qa_verdict draws, and it makes the fail visible.
                round_verdicts = [
                    v for v in (state.get("qa") or [])
                    if int(v.get("iteration") or 0) == i
                ]
                if round_verdicts:
                    artifact(state, "render_qa_verdict",
                             agui_map.qa_verdict_args(round_verdicts, iteration=i))
                if state.get("fact_pass") and state.get("voice_pass"):
                    ping(state, self.name, "loop", f"Passed on iteration {i}.")
                    state["qa_pass"] = True
                    return state
                ping(state, self.name, "loop", f"Iteration {i} failed; rewriting.", status="fail")
                await writer.run(state)
            state["qa_pass"] = bool(state.get("fact_pass") and state.get("voice_pass"))
            ping(state, self.name, "loop", "Max iterations reached.", status="ok" if state["qa_pass"] else "fail")
        return state
