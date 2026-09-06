"""In-memory run store for SSE + AG-UI. P2 only — not SQLite."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from cdr import agui_map, run_store
from shared.events import make_run_event
from shared.schemas import PatternKind, RunEvent, RunState

RUNS: dict[str, dict[str, Any]] = {}
_queues: dict[str, asyncio.Queue] = {}

# Run ids the human asked to stop. The Stop button never gates the agents; it
# ends the run early.
_cancelled: set[str] = set()

# Set once per run in service.execute_run. Lets code far from the graph state -
# mcp_client, mostly - report to the right stream without threading run_id
# through every signature. asyncio.gather copies the context, so the parallel
# research fan-out inherits it.
_current_run: contextvars.ContextVar[str] = contextvars.ContextVar("cdr_run_id", default="")


def set_current_run(run_id: str) -> None:
    _current_run.set(run_id)


def current_run() -> str:
    return _current_run.get()


def new_run(run_id: str, profile_id: str, opportunity_ids: list[str]) -> dict[str, Any]:
    rec = {
        "run_id": run_id,
        "profile_id": profile_id,
        "opportunity_ids": opportunity_ids,
        "current_agent": "CDRRootAgent",
        "events": [],
        "packages": [],
        "outreach": [],
        "qa": [],
        "agui": [],
        "status": "running",
    }
    RUNS[run_id] = rec
    _queues[run_id] = asyncio.Queue()
    return rec


def get_run(run_id: str) -> dict[str, Any] | None:
    return RUNS.get(run_id)


def cancel(run_id: str) -> None:
    _cancelled.add(run_id)


def cancelled(run_id: str) -> bool:
    return run_id in _cancelled


def clear_cancel(run_id: str) -> None:
    _cancelled.discard(run_id)


def emit(
    run_id: str,
    agent: str,
    pattern: PatternKind | str,
    summary: str,
    status: str = "ok",
    artifact_ref: str | None = None,
) -> RunEvent:
    pk = pattern if isinstance(pattern, PatternKind) else PatternKind(pattern)
    ev = make_run_event(run_id, agent, pk, summary, status=status, artifact_ref=artifact_ref)
    rec = RUNS.setdefault(run_id, {"run_id": run_id, "events": [], "agui": []})
    payload = ev.model_dump(mode="json")
    rec.setdefault("events", []).append(payload)
    rec["current_agent"] = agent
    agui = _to_agui(ev)
    rec.setdefault("agui", []).append(agui)
    q = _queues.get(run_id)
    if q is not None:
        q.put_nowait({"sse": payload, "agui": agui})
    # Write-through to Postgres. Queued, never awaited - the stream must not
    # wait on the database.
    run_store.enqueue_event(run_id, payload)
    run_store.enqueue_agui(run_id, agui)
    return ev


def emit_agui(run_id: str, event: dict[str, Any]) -> None:
    rec = RUNS.setdefault(run_id, {"run_id": run_id, "events": [], "agui": []})
    rec.setdefault("agui", []).append(event)
    q = _queues.get(run_id)
    if q is not None:
        q.put_nowait({"sse": None, "agui": event})
    run_store.enqueue_agui(run_id, event)


def emit_custom(run_id: str, name: str, value: dict[str, Any]) -> None:
    """A board-panel event (agent_trace, mcp_call, opportunities, pipeline, ...)."""
    emit_agui(run_id or current_run(), agui_map.custom(name, value, run_id or current_run()))


def emit_tool_call(run_id: str, name: str, args: dict[str, Any]) -> None:
    """One generative-UI card, as TOOL_CALL_START / _ARGS / _END."""
    rid = run_id or current_run()
    for frame in agui_map.tool_call(name, args, rid):
        emit_agui(rid, frame)


def emit_error(run_id: str, message: str) -> None:
    emit_agui(run_id, {"type": "RUN_ERROR", "runId": run_id, "message": message})


def finish(run_id: str, extra: dict[str, Any] | None = None) -> None:
    rec = RUNS.setdefault(run_id, {"run_id": run_id})
    rec["status"] = "done"
    if extra:
        rec.update(extra)
    emit_agui(run_id, {"type": "RUN_FINISHED", "runId": run_id})
    q = _queues.get(run_id)
    if q is not None:
        q.put_nowait(None)
    clear_cancel(run_id)
    # Stamp the run row and drain the write queue. Fire-and-forget so finish()
    # keeps its synchronous signature for the many call sites that use it.
    error = (extra or {}).get("error")
    asyncio.create_task(
        run_store.finish_run(
            run_id,
            status="error" if error else "done",
            result={k: rec.get(k, []) for k in ("packages", "outreach", "qa")},
            error=error,
        )
    )


def queue(run_id: str) -> asyncio.Queue:
    return _queues.setdefault(run_id, asyncio.Queue())


def as_run_state(run_id: str) -> RunState:
    rec = RUNS.get(run_id) or {"run_id": run_id, "profile_id": "", "current_agent": "", "events": []}
    return RunState(
        run_id=rec.get("run_id", run_id),
        profile_id=rec.get("profile_id", ""),
        current_agent=rec.get("current_agent", ""),
        opportunity_ids=rec.get("opportunity_ids", []),
        events=[RunEvent.model_validate(e) for e in rec.get("events", [])],
        packages=rec.get("packages", []),
        outreach=rec.get("outreach", []),
        qa=rec.get("qa", []),
    )


def _to_agui(ev: RunEvent) -> dict[str, Any]:
    """Trace rows go on the wire as CUSTOM/agent_trace - what the board renders.

    This used to emit STEP_FINISHED, which no client has ever handled; a live
    run drew an empty board. See cdr/agui_map.py for the contract.
    """
    return agui_map.agent_trace(
        agent=ev.agent,
        pattern=ev.pattern.value,
        summary=ev.summary,
        status=ev.status,
        run_id=ev.run_id,
    )
