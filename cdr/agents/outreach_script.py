from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.llm import complete_json
from shared.agent_base import Agent
from shared.schemas import OutreachDraft


class OutreachScriptAgent(Agent):
    name = "OutreachScriptAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "llm", "DM + 30s collab-call script.")
            opp = state.get("current") or {}
            data = await complete_json(
                "You are OutreachScriptAgent. Return {channel, to, subject, body} for a 30s call script.",
                f"opp={opp}\nstrategy={state.get('outreach_strategy')}",
                agent=self.name,
            )
            draft = OutreachDraft(
                opportunity_id=str(opp.get("id", "")),
                channel="call_script",
                to=str(data.get("to", "Laksa Lab")),
                subject=str(data.get("subject", "")),
                body=str(data.get("body", "")),
                status="drafted",
            )
            state.setdefault("outreach", []).append(draft.model_dump())
            name, args = agui_map.outreach_args(draft.model_dump(), opp)
            artifact(state, name, args)
            ping(state, self.name, "sequential", "Call script drafted.", artifact_ref="OutreachDraft")
        return state
