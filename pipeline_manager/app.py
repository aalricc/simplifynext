from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from shared.cors import add_cors
from shared.db import dispose, healthcheck
from observability.otel import agent_span
from pipeline_manager import db
from pipeline_manager.agents.opportunity_clerk import OpportunityClerkAgent
from pipeline_manager.agents.persist_and_schedule import PersistAndSchedule
from pipeline_manager.agents.status_tracker import StatusTrackerAgent
from pipeline_manager.agents.follow_up_planner import FollowUpPlannerAgent

app = FastAPI(title="Pipeline Manager", version="0.2.0")
add_cors(app)


@app.on_event("startup")
async def startup() -> None:
    await db.init_db()


@app.on_event("shutdown")
async def shutdown() -> None:
    await dispose()


@app.get("/health")
async def health() -> dict:
    return {"service": "pipeline_manager", "status": "ok", **(await healthcheck())}


@app.post("/pipeline/upsert")
async def upsert(request: Request) -> dict:
    payload = await request.json()
    state: dict[str, Any] = {"payload": payload}
    with agent_span(OpportunityClerkAgent.name, OpportunityClerkAgent.kind):
        state = await OpportunityClerkAgent().run(state)
    return {"ok": True, "id": state.get("id"), "record_kind": state.get("record_kind")}


@app.get("/pipeline/opportunities")
async def list_opportunities() -> dict:
    return {"opportunities": await db.list_opportunities()}


@app.get("/pipeline/opportunities/{oid}")
async def get_opportunity(oid: str) -> dict:
    record = await db.get_opportunity(oid)
    return record or {"error": "not found", "id": oid}


@app.post("/pipeline/calendar")
async def calendar(request: Request) -> dict:
    body = await request.json()
    event = await db.save_calendar_event(body)
    return {"ok": True, "event": event}


@app.post("/tools/persist_and_schedule")
async def persist_and_schedule(request: Request) -> dict:
    payload = await request.json()
    state: dict[str, Any] = {"payload": payload, "run_id": payload.get("run_id", "")}
    state = await PersistAndSchedule().run(state)
    return {
        "ok": True,
        "id": state.get("id"),
        "record_kind": state.get("record_kind"),
        "qualification": state.get("qualification"),
        "follow_up": state.get("follow_up"),
        "calendar_slots": state.get("calendar_slots", []),
    }


@app.post("/tools/update_status")
async def update_status(request: Request) -> dict:
    """Agent-as-tool entry for Engagement Listener / MCP: StatusTracker then FollowUpPlanner."""
    body = await request.json()
    state: dict[str, Any] = {"opportunity_id": body["opportunity_id"], "status": body["status"]}

    with agent_span(StatusTrackerAgent.name, StatusTrackerAgent.kind):
        state = await StatusTrackerAgent().run(state)

    if state.get("stored") is None:
        return {"ok": False, "error": "opportunity not found", "id": body["opportunity_id"]}

    with agent_span(FollowUpPlannerAgent.name, FollowUpPlannerAgent.kind):
        state = await FollowUpPlannerAgent().run(state)

    return {
        "ok": True,
        "id": state["opportunity_id"],
        "status": state["status"],
        "follow_up": state.get("follow_up"),
        "calendar_slots": state.get("calendar_slots", []),
    }


@app.get("/pipeline/memory")
async def get_memory() -> dict:
    return await db.get_memory() or {"wins": [], "losses": [], "next_bias": []}


@app.post("/pipeline/memory")
async def post_memory(request: Request) -> dict:
    # No longer mirrored to demo/maya/memory.json: with more than one creator
    # that write was a race on a single file, and the row is per-profile now.
    body = await request.json()
    return {"ok": True, "memory": await db.write_memory(body)}


@app.get("/pipeline/calendar")
async def list_calendar() -> dict:
    return {"events": await db.list_calendar_events()}


@app.get("/pipeline/rag")
async def list_rag() -> dict:
    return {"documents": await db.list_rag_documents()}


@app.post("/pipeline/rag")
async def add_rag(request: Request) -> dict:
    doc = await request.json()
    return {"ok": True, "id": await db.add_rag_document(doc)}


@app.get("/pipeline/engagement")
async def list_engagement(unread_only: bool = False) -> dict:
    return {"items": await db.list_engagement_items(unread_only=unread_only)}


@app.post("/pipeline/engagement")
async def add_engagement(request: Request) -> dict:
    body = await request.json()
    item_id = await db.add_engagement_item(
        source=body.get("source", "email"),
        payload=body.get("payload") or {},
        opportunity_id=body.get("opportunity_id"),
        classification=body.get("classification"),
    )
    return {"ok": True, "id": item_id}


@app.get("/pipeline/analytics")
async def list_analytics() -> dict:
    return {"posts": await db.list_analytics_posts()}


@app.post("/pipeline/analytics")
async def add_analytics(request: Request) -> dict:
    post = await request.json()
    return {"ok": True, "id": await db.add_analytics_post(post)}
