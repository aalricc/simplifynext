from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request

from engagement_listener.agents.engagement_ingest import EngagementIngestAgent
from engagement_listener.agents.performance_adapt import PerformanceAdaptAgent
from engagement_listener.agents.reply_classifier import ReplyClassifierAgent
from observability.otel import agent_span
from shared.cors import add_cors
from shared.db import dispose, healthcheck
from shared.http_clients import PIPELINE_MANAGER_URL, client

app = FastAPI(title="Engagement Listener", version="0.2.0")
add_cors(app)


@app.on_event("shutdown")
async def shutdown() -> None:
    await dispose()


@app.get("/health")
async def health() -> dict:
    return {"service": "engagement_listener", "status": "ok", **(await healthcheck())}


async def _pipeline(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with client(timeout=15.0) as c:
        r = await c.request(method, f"{PIPELINE_MANAGER_URL}{path}", **kwargs)
        r.raise_for_status()
        return r.json()


@app.get("/engagement/inbox")
async def inbox(unread_only: bool = False) -> dict:
    """This creator's inbox, from `engagement_items`. Was demo/maya/inbox.json."""
    return await _pipeline(
        "GET", "/pipeline/engagement", params={"unread_only": str(unread_only).lower()}
    )


async def _update_pipeline_status(opportunity_id: str, status: str) -> dict[str, Any]:
    return await _pipeline(
        "POST",
        "/tools/update_status",
        json={"opportunity_id": opportunity_id, "status": status},
    )


async def _push_memory(memory: dict[str, Any]) -> None:
    await _pipeline("POST", "/pipeline/memory", json=memory)


async def _classify_reply(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    with agent_span(EngagementIngestAgent.name, EngagementIngestAgent.kind):
        state = await EngagementIngestAgent().run({"source": source, "payload": payload})
    with agent_span(ReplyClassifierAgent.name, ReplyClassifierAgent.kind):
        state = await ReplyClassifierAgent().run(state)

    if state.get("status") and state.get("opportunity_id"):
        try:
            state["pipeline_update"] = await _update_pipeline_status(
                state["opportunity_id"], state["status"]
            )
        except httpx.HTTPError as exc:
            state["pipeline_update_error"] = str(exc)

    return state


@app.post("/engagement/ingest")
async def ingest(request: Request) -> dict:
    """The real inbound path: one reply, classified and persisted."""
    body = await request.json()
    payload = dict(body.get("payload") or {})
    if body.get("opportunity_id") and "opportunity_id" not in payload:
        payload["opportunity_id"] = body["opportunity_id"]

    result = await _classify_reply(body.get("source", "email"), payload)

    if body.get("persist", True):
        try:
            await _pipeline(
                "POST",
                "/pipeline/engagement",
                json={
                    "source": body.get("source", "email"),
                    "payload": payload,
                    "opportunity_id": result.get("opportunity_id"),
                    "classification": {
                        "label": result.get("label"),
                        "status": result.get("status"),
                    },
                },
            )
        except httpx.HTTPError as exc:
            result["persist_error"] = str(exc)

    return {"ok": True, "classification": result}


@app.post("/engagement/process_week")
async def process_week(request: Request) -> dict:
    """Classify this creator's unprocessed inbox and re-derive memory.

    Replaces `POST /engagement/replay_maya_week2`, which read one hardcoded
    file for one persona. Everything here is scoped to the profile on the
    request, so it works for any creator.
    """
    inbox_data = await _pipeline("GET", "/pipeline/engagement")
    analytics = await _pipeline("GET", "/pipeline/analytics")

    replies = [
        await _classify_reply(item.get("source", "email"), item)
        for item in inbox_data.get("items", [])
    ]

    with agent_span(PerformanceAdaptAgent.name, PerformanceAdaptAgent.kind):
        adapt_state = await PerformanceAdaptAgent().run(
            {"posts": analytics.get("posts", [])}
        )
    memory = adapt_state["memory"]

    try:
        await _push_memory(memory)
    except httpx.HTTPError as exc:
        memory = {**memory, "push_error": str(exc)}

    return {"ok": True, "replies": replies, "memory": memory}
