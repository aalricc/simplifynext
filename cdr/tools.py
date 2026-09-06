"""Agent-as-tool wrappers used by CDRRootAgent."""

from __future__ import annotations

from typing import Any

from cdr.agents.outreach_pipeline import OutreachPipeline
from cdr.agents.parallel_research import ParallelResearch
from cdr.agents.proposal_generation import ProposalGenerationAgent
from cdr.agents.refinement_loop import RefinementLoop
from cdr import agui_map
from cdr.mcp_client import find_opportunities as mcp_find
from cdr.mcp_client import persist_and_schedule as mcp_persist
from cdr.runtime import emit, emit_custom, emit_tool_call


async def find_opportunities(state: dict[str, Any]) -> dict[str, Any]:
    emit(str(state.get("run_id", "")), "CDRRootAgent", "tool", "find_opportunities → P1/MCP")
    profile = state.get("profile") or {}
    data = await mcp_find(
        {
            # No demo-persona defaults: searching Singapore hawker food for a
            # creator who never said either is a confident wrong answer.
            "profile_id": profile.get("id", ""),
            "niche": profile.get("niche", ""),
            "city": profile.get("city", ""),
            "limit": 8,
            "profile": profile,
        }
    )
    opps = data.get("opportunities") or state.get("opportunities") or []
    state["opportunities"] = opps
    run_id = str(state.get("run_id", ""))
    if opps:
        emit_custom(run_id, "opportunities", agui_map.opportunities(opps, run_id)["value"])
    return state


async def research_opportunity(state: dict[str, Any]) -> dict[str, Any]:
    emit(str(state.get("run_id", "")), "CDRRootAgent", "tool", "research_opportunity → ParallelResearch")
    from cdr.agents.research_lead import ResearchLeadAgent

    await ResearchLeadAgent().run(state)
    await ParallelResearch().run(state)
    return state


async def generate_proposal(state: dict[str, Any]) -> dict[str, Any]:
    emit(str(state.get("run_id", "")), "CDRRootAgent", "tool", "generate_proposal → ProposalGenerationAgent")
    await ProposalGenerationAgent().run(state)
    return state


async def run_qa(state: dict[str, Any]) -> dict[str, Any]:
    emit(str(state.get("run_id", "")), "CDRRootAgent", "tool", "run_qa → RefinementLoop")
    await RefinementLoop().run(state)
    return state


async def draft_outreach(state: dict[str, Any]) -> dict[str, Any]:
    emit(str(state.get("run_id", "")), "CDRRootAgent", "tool", "draft_outreach → OutreachPipeline")
    await OutreachPipeline().run(state)
    return state


async def persist_and_schedule(state: dict[str, Any]) -> dict[str, Any]:
    run_id = str(state.get("run_id", ""))
    emit(run_id, "CDRRootAgent", "tool", "persist_and_schedule → MCP/P3")
    current = state.get("current") or {}
    opportunity_id = current.get("id")
    payload = {
        "run_id": state.get("run_id"),
        "opportunity_id": opportunity_id,
        # The opportunity record itself, not just its id. Without this the
        # clerk had nothing to upsert -- it classified the bundle as "unknown"
        # and filed it as an artifact, so the board's opportunity table and
        # kanban stayed empty for every real run.
        "opportunity": {**current, "status": "outreached"} if current else None,
        "package": state.get("package"),
        "outreach": state.get("outreach"),
        "qa": state.get("qa"),
        "brief": state.get("brief"),
        "status": "outreached",
    }
    result = await mcp_persist(payload)
    state["persist_result"] = result

    # Move the card on the kanban and put the week on the calendar strip. The
    # enum here is P3's; agui_map maps it to the board's column vocabulary.
    if opportunity_id:
        emit_custom(run_id, "pipeline",
                    agui_map.pipeline([(opportunity_id, "outreached")], run_id)["value"])
    if state.get("package"):
        emit_tool_call(run_id, "render_calendar_week",
                       agui_map.calendar_args(state["package"]))
    if not (isinstance(result, dict) and result.get("ok", True)):
        emit(run_id, "CDRRootAgent", "tool", "persist_and_schedule did not confirm.",
             status="fail")
    return state
