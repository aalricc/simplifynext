"""AG-UI endpoint: stream the board's event contract for CopilotKit generative UI."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from cdr.runtime import get_run, queue
from cdr.service import ProfileMissing, execute_run, start_run

router = APIRouter()


@router.post("/ag-ui")
async def ag_ui_run(request: Request) -> StreamingResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Every real AG-UI client (@ag-ui/client's HttpAgent, CopilotKit) sends a
    # runId it generated itself, and so does the UI proxy. Treating "has a
    # runId" as "this run already exists" meant those clients started nothing
    # and hung on an empty queue forever. Only an id we already know is a
    # re-attach; anything else starts a run under the id the client chose, so
    # the client can still correlate the stream it asked for.
    run_id = str(body.get("runId") or body.get("run_id") or "").strip()
    try:
        if not run_id:
            run_id = await start_run(body)
            asyncio.create_task(execute_run(run_id, body))
        elif get_run(run_id) is None:
            await start_run(body, run_id=run_id)
            asyncio.create_task(execute_run(run_id, body))
    except ProfileMissing as exc:
        # No verified tenant: refuse rather than run a campaign for nobody.
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'RUN_ERROR', 'message': str(exc)})}\n\n"]),
            media_type="text/event-stream",
        )

    async def gen():
        # Replay what a re-attaching client missed, then follow the queue from
        # where the replay stopped. RUN_STARTED is emitted by execute_run and
        # arrives through one of those two paths - never both, or clients see
        # the run start several times.
        replayed = 0
        rec = get_run(run_id)
        if rec:
            for ev in list(rec.get("agui") or []):
                replayed += 1
                yield f"data: {json.dumps(ev)}\n\n"

        q = queue(run_id)
        seen = 0
        while True:
            item = await q.get()
            if item is None:
                break
            event = item.get("agui")
            if not event:
                continue
            seen += 1
            if seen <= replayed:
                continue
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
