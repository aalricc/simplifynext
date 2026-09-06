from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.llm import complete_json
from shared.agent_base import Agent
from shared.schemas import ContentPackage, WeekPlanItem


# Groq returns a week plan that is *about* right but rarely field-for-field:
# {day, slot, idea} instead of {hook, format, platform, posting_slot}. Strict
# validation turned that into a dead run, so accept the near-misses instead.
_ALIASES = {
    "hook": ("hook", "idea", "title", "concept", "topic"),
    "format": ("format", "type", "content_type", "style"),
    "platform": ("platform", "channel", "network"),
    "posting_slot": ("posting_slot", "slot", "when", "time", "day", "post_at"),
}
_DEFAULTS = {
    "hook": "hawker tip",
    "format": "talking-head",
    "platform": "tiktok",
    "posting_slot": "Fri 19:30 SGT",
}


def _captions(raw: Any) -> dict[str, str]:
    """captions is dict[str, str]; the model sometimes sends a list or nests."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {f"caption_{i + 1}": str(v) for i, v in enumerate(raw)}
    return {"caption_1": str(raw)} if raw else {}


def _week_item(raw: dict[str, Any]) -> WeekPlanItem:
    values = {}
    for field, names in _ALIASES.items():
        found = next((raw[n] for n in names if raw.get(n)), None)
        values[field] = str(found) if found is not None else _DEFAULTS[field]
    # A day and a time arriving as separate keys should stay one slot string.
    if raw.get("day") and raw.get("slot"):
        values["posting_slot"] = f"{raw['day']} {raw['slot']}"
    return WeekPlanItem(**values)


class ProposalGenerationAgent(Agent):
    name = "ProposalGenerationAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "llm", "Week plan + hero script + captions + CTA.")
            opp = state.get("current") or {}
            data = await complete_json(
                "You are ProposalGenerationAgent. Return ContentPackage JSON. "
                "week_plan must have exactly 3 items. Include an unsourced calorie claim in hero_script "
                "on first draft so QA can fail once (Maya demo).",
                f"profile={state.get('profile')}\nbrief={state.get('brief')}\nopp={opp}",
                agent=self.name,
            )
            oid = str(opp.get("id") or data.get("opportunity_id") or "")
            plan = [_week_item(x) for x in data.get("week_plan", []) if isinstance(x, dict)][:3]
            while len(plan) < 3:
                plan.append(
                    WeekPlanItem(
                        hook="follow-up hawker tip",
                        format="talking-head",
                        platform="tiktok",
                        posting_slot="Fri 19:30 SGT",
                    )
                )
            pkg = ContentPackage(
                opportunity_id=oid,
                week_plan=plan,
                hero_script=str(data.get("hero_script", "")),
                captions=_captions(data.get("captions")),
                cta=str(data.get("cta", "")),
                sources=[str(s) for s in (data.get("sources") or [])],
            )
            state["package"] = pkg.model_dump()
            state["package_version"] = 1
            artifact(state, "render_content_package",
                     agui_map.content_package_args(state["package"], version=1))
            ping(state, self.name, "llm", "Draft package ready.", artifact_ref="ContentPackage")
        return state
