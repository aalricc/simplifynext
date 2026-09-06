from __future__ import annotations

import asyncio
import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from cdr import run_store
from cdr.agui import router as agui_router
from cdr.runtime import as_run_state, cancel, get_run, queue
from cdr.service import ProfileMissing, execute_run, start_run
from harness.agentcore import runtime_payload
from shared.cors import add_cors
from shared.db import dispose, healthcheck

load_dotenv()

app = FastAPI(title="CDR Agent", version="0.3.0")
add_cors(app)
app.include_router(agui_router)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await run_store.shutdown()
    await dispose()


@app.get("/health")
async def health() -> dict:
    return {
        "service": "cdr",
        "status": "ok",
        "runtime": runtime_payload(),
        **(await healthcheck()),
    }


@app.post("/cdr/run")
async def run(request: Request) -> dict:
    body = await request.json()
    try:
        run_id = await start_run(body)
    except ProfileMissing as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    asyncio.create_task(execute_run(run_id, body))
    return {"run_id": run_id}


@app.post("/cdr/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict:
    """End a run early. This never gates the agents -- it stops the stream."""
    cancel(run_id)
    return {"stopped": run_id}


@app.get("/cdr/runs/{run_id}")
async def get_run_http(run_id: str) -> dict:
    rec = get_run(run_id)
    if not rec:
        # Not in memory: the service may have restarted mid-demo. History now
        # outlives the process, so look it up instead of giving up.
        events = await run_store.load_run_events(run_id)
        if not events:
            return {"run_id": run_id, "events": [], "note": "unknown run"}
        return {"run_id": run_id, "events": events, "status": "done", "source": "database"}
    return as_run_state(run_id).model_dump(mode="json") | {
        "status": rec.get("status"),
        "packages": rec.get("packages", []),
        "outreach": rec.get("outreach", []),
        "qa": rec.get("qa", []),
        "error": rec.get("error"),
    }


@app.get("/cdr/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    async def gen():
        rec = get_run(run_id)
        if rec:
            for ev in rec.get("events") or []:
                yield f"data: {json.dumps(ev)}\n\n"
        q = queue(run_id)
        while True:
            item = await q.get()
            if item is None:
                yield "data: {\"status\": \"done\"}\n\n"
                break
            if item.get("sse"):
                yield f"data: {json.dumps(item['sse'])}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
