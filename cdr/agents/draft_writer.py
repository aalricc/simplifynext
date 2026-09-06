from typing import Any

from cdr import agui_map
from cdr.agents._util import artifact, ping, with_span
from cdr.agents.proposal_generation import _captions
from cdr.llm import complete_json
from shared.agent_base import Agent


class DraftWriterAgent(Agent):
    name = "DraftWriterAgent"
    kind = "llm"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        with with_span(self.name, self.kind, state):
            ping(state, self.name, "loop", "Rewrite package from QA issues.")
            pkg = dict(state.get("package") or {})
            data = await complete_json(
                "You are DraftWriterAgent. Rewrite hero_script/captions/cta/sources. "
                "Remove unsourced calorie claims. Keep Maya voice and named ingredients. JSON only.",
                f"package={pkg}\nmust_fix={state.get('must_fix')}\nrewrite=true",
                agent=self.name,
            )
            # Keep the rewrite inside ContentPackage's shape - a rewrite that
            # returns captions as a list used to crash the run downstream.
            pkg["hero_script"] = str(data.get("hero_script") or pkg.get("hero_script") or "")
            pkg["captions"] = _captions(data.get("captions") or pkg.get("captions"))
            pkg["cta"] = str(data.get("cta") or pkg.get("cta") or "")
            pkg["sources"] = [str(s) for s in (data.get("sources") or pkg.get("sources") or [])]
            state["package"] = pkg
            # A second card, not a replacement: the fail-then-fix is the story,
            # so v1 has to stay on screen next to v2.
            version = int(state.get("package_version") or 1) + 1
            state["package_version"] = version
            artifact(state, "render_content_package", agui_map.content_package_args(
                pkg, version=version, changes=list(state.get("must_fix") or [])))
            ping(state, self.name, "loop", "Rewrite applied.", artifact_ref="ContentPackage")
        return state
