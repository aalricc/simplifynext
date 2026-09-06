"""Durable run history, written alongside the live stream.

`cdr/runtime.py` keeps runs in a module-level dict so the SSE stream stays
fast. That dict is still the live path -- this module mirrors it into Postgres
so a run survives a restart, a browser refresh can rejoin mid-run, and finished
runs can be replayed.

THE STREAM NEVER WAITS ON THE DATABASE
--------------------------------------
Emitters call `enqueue_*`, which only puts a dict on an in-memory queue. One
background task per process drains it in order. A slow or briefly unreachable
database therefore delays persistence, never the trace panel -- the whole point
of that panel is that it appears while the agents work.

`agui_events` rows are the same shape as one line of `demo/fixtures/*.jsonl`,
which is what lets this table replace the fixture replay entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from sqlalchemy import delete, select

from shared.db import session
from shared.models import AguiEvent, Run, RunEvent, utcnow

# (kind, run_id, payload) -- kind is "event" or "agui"
_queue: asyncio.Queue[tuple[str, str, dict[str, Any]] | None] | None = None
_writer: asyncio.Task | None = None
_seq: dict[str, int] = {}


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# background writer
# ---------------------------------------------------------------------------


def _ensure_writer() -> asyncio.Queue:
    global _queue, _writer
    if _queue is None:
        _queue = asyncio.Queue()
    if _writer is None or _writer.done():
        _writer = asyncio.create_task(_drain())
    return _queue


async def _drain() -> None:
    assert _queue is not None
    while True:
        item = await _queue.get()
        if item is None:
            _queue.task_done()
            return
        kind, run_id, payload = item
        try:
            async with session() as s:
                if kind == "event":
                    s.add(RunEvent(run_id=run_id, **payload))
                else:
                    s.add(AguiEvent(run_id=run_id, **payload))
        except Exception as exc:  # noqa: BLE001
            # A persistence failure must not kill the run in progress. The
            # board already has the frame; this row is history.
            print(f"[run_store] dropped {kind} for run {run_id}: {exc}")
        finally:
            _queue.task_done()


async def flush() -> None:
    """Wait for queued writes to land. Called at the end of a run."""
    if _queue is not None:
        await _queue.join()


async def shutdown() -> None:
    global _writer
    if _queue is not None:
        await _queue.join()
        await _queue.put(None)
    if _writer is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await _writer
    _writer = None


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


async def start_run(run_id: str, profile_id: str, week: int = 1) -> None:
    pid = _as_uuid(profile_id)
    if pid is None:
        return
    _seq[run_id] = 0
    _ensure_writer()
    async with session() as s:
        existing = await s.get(Run, run_id)
        if existing is None:
            s.add(Run(id=run_id, profile_id=pid, week=week, status="running"))


def enqueue_event(run_id: str, event: dict[str, Any]) -> None:
    """One trace row (agent / pattern / status / summary)."""
    if not run_id:
        return
    q = _ensure_writer()
    _seq[run_id] = _seq.get(run_id, 0) + 1
    q.put_nowait(
        (
            "event",
            run_id,
            {
                "seq": _seq[run_id],
                "agent": str(event.get("agent", ""))[:120],
                "pattern": str(event.get("pattern", "custom"))[:20],
                "status": str(event.get("status", "ok"))[:20],
                "summary": str(event.get("summary", "")),
                "artifact_ref": event.get("artifact_ref"),
            },
        )
    )


def enqueue_agui(run_id: str, payload: dict[str, Any]) -> None:
    """One raw AG-UI frame, in order."""
    if not run_id:
        return
    q = _ensure_writer()
    _seq[run_id] = _seq.get(run_id, 0) + 1
    q.put_nowait(("agui", run_id, {"seq": _seq[run_id], "payload": payload}))


async def finish_run(
    run_id: str,
    status: str = "done",
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    await flush()
    async with session() as s:
        row = await s.get(Run, run_id)
        if row is not None:
            row.status = status
            row.finished_at = utcnow()
            row.result = result or {}
            row.error = error
    _seq.pop(run_id, None)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


async def list_runs(profile_id: str, limit: int = 25) -> list[dict[str, Any]]:
    pid = _as_uuid(profile_id)
    if pid is None:
        return []
    async with session() as s:
        rows = (
            await s.scalars(
                select(Run)
                .where(Run.profile_id == pid)
                .order_by(Run.started_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "run_id": r.id,
                "week": r.week,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "error": r.error,
            }
            for r in rows
        ]


async def load_agui_events(
    run_id: str, profile_id: str | None = None
) -> list[dict[str, Any]] | None:
    """Every frame of a run, in order. None if the run is not this profile's."""
    async with session() as s:
        run = await s.get(Run, run_id)
        if run is None:
            return None
        if profile_id is not None and str(run.profile_id) != str(profile_id):
            # Do not distinguish "not yours" from "does not exist".
            return None
        rows = (
            await s.scalars(
                select(AguiEvent)
                .where(AguiEvent.run_id == run_id)
                .order_by(AguiEvent.seq)
            )
        ).all()
        return [r.payload for r in rows]


async def load_run_events(run_id: str) -> list[dict[str, Any]]:
    async with session() as s:
        rows = (
            await s.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
            )
        ).all()
        return [
            {
                "ts": r.ts.isoformat(),
                "agent": r.agent,
                "pattern": r.pattern,
                "status": r.status,
                "summary": r.summary,
                "artifact_ref": r.artifact_ref,
                "run_id": r.run_id,
            }
            for r in rows
        ]


async def purge_run(run_id: str) -> None:
    async with session() as s:
        await s.execute(delete(Run).where(Run.id == run_id))
