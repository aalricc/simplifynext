from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.llm import complete_json
from shared.agent_base import Agent
from shared.schemas import OutreachDraft


class PitchEmailAgent(Agent):
    name = "PitchEmailAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "llm", "Brand pitch email.")
            opp = state.get("current") or {}
            data = await complete_json(
                "You are PitchEmailAgent. Return {channel, to, subject, body}.",
                f"opp={opp}\npackage={state.get('package')}\nstrategy={state.get('outreach_strategy')}",
                agent=self.name,
            )
            draft = OutreachDraft(
                opportunity_id=str(opp.get("id", "")),
                channel="email",
                to=str(data.get("to", "hello@laksalab.sg")),
                subject=str(data.get("subject", "Collab")),
                body=str(data.get("body", "")),
                status="drafted",
            )
            dm = OutreachDraft(
                opportunity_id=str(opp.get("id", "")),
                channel="dm",
                to=str(data.get("to", "Laksa Lab")),
                subject="",
                body="Maya here — leftover-stock laksa reel + 3-post trial. Paste in the shot if you want it. 15 min call?",
                status="drafted",
            )
            state.setdefault("outreach", []).extend([draft.model_dump(), dm.model_dump()])
            for row in (draft.model_dump(), dm.model_dump()):
                name, args = agui_map.outreach_args(row, opp)
                artifact(state, name, args)
            ping(state, self.name, "sequential", "Email + DM drafted.", artifact_ref="OutreachDraft")
        return state
