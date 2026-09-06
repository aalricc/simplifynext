"""LangGraph campaign graph.

Workflow:
  START → load_opportunities → CDRRootAgent (tools: research, propose, qa, outreach, persist) → END

Inside each opportunity the root calls:
  ResearchLead → ParallelResearch (4-way gather) → ProposalGeneration
  → RefinementLoop (fact + voice, rewrite, max 3) → OutreachPipeline → MCP persist
"""

from __future__ import annotations

from typing import Any, Callable

from cdr import agui_map
from cdr.agents.root import CDRRootAgent
from cdr.mcp_client import find_opportunities
from cdr.runtime import emit, emit_custom, finish


async def load_opportunities(state: dict[str, Any]) -> dict[str, Any]:
    run_id = str(state.get("run_id", ""))
    emit(run_id, "OpportunityLoader", "tool", "Load opportunities (P1 or Maya seed).")
    if state.get("opportunities"):
        return state
    profile = state.get("profile") or {}
    data = await find_opportunities(
        {
            # No persona defaults. A run that lost its creator must fail
            # visibly, not search Singapore hawker food for whoever asked.
            "profile_id": profile.get("id", ""),
            "niche": profile.get("niche", ""),
            "city": profile.get("city", ""),
            "limit": 8,
            "profile": profile,
        }
    )
    state["opportunities"] = data.get("opportunities") or []
    if state["opportunities"]:
        emit_custom(run_id, "opportunities",
                    agui_map.opportunities(state["opportunities"], run_id)["value"])
    return state


def build_graph() -> Callable:
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception:
        return None  # type: ignore[return-value]

    g = StateGraph(dict)
    g.add_node("load", load_opportunities)
    g.add_node("root", CDRRootAgent().run)
    g.add_edge(START, "load")
    g.add_edge("load", "root")
    g.add_edge("root", END)
    return g.compile()


_COMPILED = None


def compiled():
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED


async def run_campaign(state: dict[str, Any]) -> dict[str, Any]:
    graph = compiled()
    if graph is not None:
        result = await graph.ainvoke(state)
        finish(
            str(result.get("run_id", state.get("run_id"))),
            {
                "packages": result.get("packages", []),
                "outreach": result.get("outreach", []),
                "qa": result.get("qa", []),
            },
        )
        return result
    state = await load_opportunities(state)
    state = await CDRRootAgent().run(state)
    finish(
        str(state.get("run_id")),
        {
            "packages": state.get("packages", []),
            "outreach": state.get("outreach", []),
            "qa": state.get("qa", []),
        },
    )
    return state
