from typing import Any

from cdr import agui_map
from cdr.agents._util import panel, ping, with_span
from cdr.llm import complete_json
from cdr import tools
from shared.agent_base import Agent


def _mark(state: dict[str, Any], opps: list, status: str) -> None:
    """Move cards across the kanban as the run advances."""
    updates = [(o.get("id"), status) for o in opps if isinstance(o, dict) and o.get("id")]
    if updates:
        panel(state, "pipeline", agui_map.pipeline(updates)["value"])


class CDRRootAgent(Agent):
    name = "CDRRootAgent"
    kind = "custom"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """DeepAgents-style root: plan, then call subgraphs as tools."""
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "custom", "Planning campaign; tools = research/propose/qa/outreach/persist.")
            plan = await complete_json(
                "You are CDRRootAgent. Return {plan: string[], selected_ids: string[]}.",
                f"profile={state.get('profile')}\nopportunities={state.get('opportunities')}",
                agent=self.name,
            )
            state["root_plan"] = plan.get("plan", [])
            selected_ids = list(plan.get("selected_ids") or [])
            opps = list(state.get("opportunities") or [])
            by_id = {o.get("id"): o for o in opps if isinstance(o, dict)}
            chosen = [by_id[i] for i in selected_ids if i in by_id]
            if not chosen:
                chosen = sorted(opps, key=lambda o: int(o.get("score") or 0), reverse=True)[:2]
            state["selected"] = chosen
            ping(state, self.name, "custom", f"Selected {[c.get('id') for c in chosen]}")
            _mark(state, chosen, "researched")

            if not state.get("opportunities"):
                await tools.find_opportunities(state)
                opps = list(state.get("opportunities") or [])
                by_id = {o.get("id"): o for o in opps if isinstance(o, dict)}
                chosen = [by_id[i] for i in selected_ids if i in by_id] or opps[:2]
                state["selected"] = chosen

            packages: list = []
            outreach: list = []
            qa: list = []
            for opp in state.get("selected") or []:
                state["current"] = opp
                state["package"] = None
                state["brief"] = None
                state["must_fix"] = []
                state["fact_pass"] = False
                state["voice_pass"] = False
                state["qa"] = []
                state["outreach"] = []
                await tools.research_opportunity(state)
                await tools.generate_proposal(state)
                await tools.run_qa(state)
                _mark(state, [opp], "packaged")
                pause = str(state.get("pause_before_send") or "").lower() in ("1", "true")
                if pause:
                    ping(state, self.name, "custom", "PAUSE_BEFORE_SEND — drafts only.", status="awaiting_send")
                else:
                    await tools.draft_outreach(state)
                    await tools.persist_and_schedule(state)
                if state.get("package"):
                    packages.append(state["package"])
                outreach.extend(state.get("outreach") or [])
                qa.extend(state.get("qa") or [])

            state["packages"] = packages
            state["outreach"] = outreach
            state["qa"] = qa
            ping(state, self.name, "custom", "Campaign complete.")
        return state
