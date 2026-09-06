from typing import Any

from shared.agent_base import Agent
from observability.otel import agent_span
from pipeline_manager import db
from pipeline_manager.agents.opportunity_clerk import OpportunityClerkAgent
from pipeline_manager.agents.qualification import QualificationAgent
from pipeline_manager.agents.follow_up_planner import FollowUpPlannerAgent
from pipeline_manager.agents.calendar_assistant import CalendarAssistantAgent

# Sub-records the CDR sends alongside the opportunity, each filed as its own
# artifact row so the board's artifact drawer can find them by kind.
ARTIFACT_KINDS = ("package", "outreach", "qa", "brief")


class PersistAndSchedule(Agent):
    name = "PersistAndSchedule"
    kind = "sequential"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Clerk → qualify → follow-up → calendar, then file the artifacts.

        The CDR sends one bundle per opportunity: the opportunity record plus
        its package, outreach, QA verdicts and research brief. Earlier this
        arrived without the opportunity record, so `classify_kind` saw no
        type/score, labelled the whole bundle "unknown", and nothing ever
        reached the opportunities table.
        """
        run_id = state.get("run_id", "")
        payload = dict(state.get("payload") or {})

        # Unwrap the bundle: the clerk upserts the opportunity, the rest are
        # artifacts hung off it.
        opportunity = payload.get("opportunity")
        if isinstance(opportunity, dict) and opportunity:
            record = dict(opportunity)
            record.setdefault("id", payload.get("opportunity_id"))
            if payload.get("status"):
                record.setdefault("status", payload["status"])
            state["payload"] = record

        steps = (
            OpportunityClerkAgent(),
            QualificationAgent(),
            FollowUpPlannerAgent(),
            CalendarAssistantAgent(),
        )
        for step in steps:
            with agent_span(step.name, step.kind, run_id):
                state = await step.run(state)

        opportunity_id = state.get("id") or payload.get("opportunity_id")
        saved = []
        for kind in ARTIFACT_KINDS:
            value = payload.get(kind)
            if not value:
                continue
            record = value if isinstance(value, dict) else {kind: value}
            artifact_id = await db.save_artifact(opportunity_id, kind, record)
            saved.append({"kind": kind, "id": artifact_id})

        state["artifacts"] = saved
        return state
