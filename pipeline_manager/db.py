"""Pipeline persistence. Single writer: this service.

Was SQLite with no tenant column anywhere. Now every query is scoped to the
creator profile in context (shared/tenant.py), which arrives on the request
headers and is verified by the tenant middleware.

`require_profile()` raises when the tenant is missing rather than defaulting,
because the quiet alternative -- reading or writing whatever row matches the
id -- is a cross-tenant leak. Opportunity ids are only unique *within* a
profile, so `(profile_id, id)` is the real key everywhere below.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from shared.db import session
from shared.models import (
    AnalyticsPost,
    Artifact,
    CalendarEvent,
    EngagementItem,
    Memory,
    Opportunity,
    RagDocument,
)
from shared.tenant import require_profile


def _pid() -> uuid.UUID:
    """The profile this request belongs to, as a UUID."""
    raw = require_profile()
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise ValueError(f"profile id {raw!r} is not a UUID") from exc


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def init_db() -> None:
    """Kept for the startup hooks that call it.

    Schema now comes from `alembic upgrade head`. On SQLite (tests, keyless
    local dev) there is no migration story worth having, so create the tables
    directly there and do nothing on a real server.
    """
    from shared.db import create_all, is_sqlite

    if is_sqlite():
        await create_all()


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------


def _row_to_record(row: Opportunity) -> dict[str, Any]:
    record = dict(row.record or {})
    record["id"] = row.id
    record["status"] = row.status
    if row.qualification:
        record["qualification"] = row.qualification
    return record


async def upsert_opportunity(record: dict[str, Any]) -> dict[str, Any]:
    """Merge into the existing row for this profile, or insert a new one."""
    pid = _pid()
    oid = record["id"]
    async with session() as s:
        row = await s.get(Opportunity, {"profile_id": pid, "id": oid})
        if row is not None:
            merged = dict(row.record or {})
            merged.update(record)
            status = record.get("status") or row.status or "new"
            merged["status"] = status
            row.record = merged
            row.status = status
            stored = _row_to_record(row)
        else:
            status = record.get("status", "new")
            merged = {**record, "status": status}
            row = Opportunity(profile_id=pid, id=oid, record=merged, status=status)
            s.add(row)
            stored = dict(merged)
            stored["id"] = oid
    return stored


async def get_opportunity(oid: str) -> dict[str, Any] | None:
    pid = _pid()
    async with session() as s:
        row = await s.get(Opportunity, {"profile_id": pid, "id": oid})
        return _row_to_record(row) if row is not None else None


async def list_opportunities() -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        rows = (
            await s.scalars(
                select(Opportunity)
                .where(Opportunity.profile_id == pid)
                .order_by(Opportunity.updated_at.desc())
            )
        ).all()
        return [_row_to_record(r) for r in rows]


async def update_status(oid: str, status: str) -> dict[str, Any] | None:
    pid = _pid()
    async with session() as s:
        row = await s.get(Opportunity, {"profile_id": pid, "id": oid})
        if row is None:
            return None
        record = dict(row.record or {})
        record["status"] = status
        row.record = record
        row.status = status
        return _row_to_record(row)


async def set_qualification(oid: str, qualification: str) -> None:
    pid = _pid()
    async with session() as s:
        row = await s.get(Opportunity, {"profile_id": pid, "id": oid})
        if row is not None:
            row.qualification = qualification


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


async def save_artifact(
    opportunity_id: str | None, kind: str, record: dict[str, Any]
) -> int:
    pid = _pid()
    async with session() as s:
        row = Artifact(
            profile_id=pid, opportunity_id=opportunity_id, kind=kind, record=record
        )
        s.add(row)
        await s.flush()
        return int(row.id)


async def list_artifacts(kind: str | None = None) -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        stmt = select(Artifact).where(Artifact.profile_id == pid)
        if kind:
            stmt = stmt.where(Artifact.kind == kind)
        rows = (await s.scalars(stmt.order_by(Artifact.created_at.desc()))).all()
        return [
            {
                "id": r.id,
                "opportunity_id": r.opportunity_id,
                "kind": r.kind,
                "record": r.record,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


async def save_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
    pid = _pid()
    eid = event.get("id") or (
        f"cal-{event.get('opportunity_id')}-{event['kind']}-{event['slot']}"
    )
    slot = _parse_dt(event["slot"])
    async with session() as s:
        row = await s.get(CalendarEvent, {"profile_id": pid, "id": eid})
        if row is None:
            row = CalendarEvent(
                profile_id=pid,
                id=eid,
                opportunity_id=event.get("opportunity_id"),
                slot=slot,
                kind=event["kind"],
                title=event.get("title", ""),
            )
            s.add(row)
        else:
            row.slot = slot
            row.title = event.get("title", row.title)
    return {**event, "id": eid, "slot": slot.isoformat()}


async def list_calendar_events() -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        rows = (
            await s.scalars(
                select(CalendarEvent)
                .where(CalendarEvent.profile_id == pid)
                .order_by(CalendarEvent.slot)
            )
        ).all()
        return [
            {
                "id": r.id,
                "opportunity_id": r.opportunity_id,
                "slot": r.slot.isoformat(),
                "kind": r.kind,
                "title": r.title,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


async def get_memory() -> dict[str, Any] | None:
    pid = _pid()
    async with session() as s:
        row = await s.get(Memory, pid)
        if row is None:
            return None
        return {
            "wins": list(row.wins or []),
            "losses": list(row.losses or []),
            "next_bias": list(row.next_bias or []),
        }


async def write_memory(memory: dict[str, Any]) -> dict[str, Any]:
    pid = _pid()
    normalized = {
        "wins": list(memory.get("wins") or []),
        "losses": list(memory.get("losses") or []),
        "next_bias": list(memory.get("next_bias") or []),
    }
    async with session() as s:
        row = await s.get(Memory, pid)
        if row is None:
            s.add(Memory(profile_id=pid, **normalized))
        else:
            row.wins = normalized["wins"]
            row.losses = normalized["losses"]
            row.next_bias = normalized["next_bias"]
    return normalized


# ---------------------------------------------------------------------------
# RAG corpus -- was demo/maya/rag_corpus.json
# ---------------------------------------------------------------------------


async def list_rag_documents() -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        rows = (
            await s.scalars(select(RagDocument).where(RagDocument.profile_id == pid))
        ).all()
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "text": r.body,
                "notes": r.body,
                "type": r.doc_type,
                "platform": r.platform,
                "tags": list(r.tags or []),
                "source_url": r.source_url,
            }
            for r in rows
        ]


async def add_rag_document(doc: dict[str, Any]) -> str:
    pid = _pid()
    async with session() as s:
        row = RagDocument(
            profile_id=pid,
            title=str(doc.get("title", "")),
            body=str(doc.get("text") or doc.get("notes") or doc.get("body") or ""),
            doc_type=str(doc.get("type", "")),
            platform=str(doc.get("platform", "")),
            tags=list(doc.get("tags") or []),
            source_url=str(doc.get("source_url", "")),
        )
        s.add(row)
        await s.flush()
        return str(row.id)


# ---------------------------------------------------------------------------
# engagement -- was demo/maya/inbox.json + analytics_week1.json
# ---------------------------------------------------------------------------


async def list_engagement_items(unread_only: bool = False) -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        stmt = select(EngagementItem).where(EngagementItem.profile_id == pid)
        if unread_only:
            stmt = stmt.where(EngagementItem.status == "unread")
        rows = (await s.scalars(stmt.order_by(EngagementItem.received_at))).all()
        return [
            {
                "id": str(r.id),
                "source": r.source,
                **(r.payload or {}),
                "opportunity_id": r.opportunity_id,
                "status": r.status,
                "classification": r.classification,
                "received_at": r.received_at.isoformat(),
            }
            for r in rows
        ]


async def add_engagement_item(
    source: str,
    payload: dict[str, Any],
    opportunity_id: str | None = None,
    classification: dict[str, Any] | None = None,
) -> str:
    pid = _pid()
    async with session() as s:
        row = EngagementItem(
            profile_id=pid,
            source=source,
            payload=payload,
            opportunity_id=opportunity_id or payload.get("opportunity_id"),
            classification=classification,
        )
        s.add(row)
        await s.flush()
        return str(row.id)


async def set_engagement_classification(
    item_id: str, classification: dict[str, Any]
) -> None:
    pid = _pid()
    async with session() as s:
        row = await s.get(EngagementItem, uuid.UUID(item_id))
        if row is not None and row.profile_id == pid:
            row.classification = classification
            row.status = "classified"


async def list_analytics_posts() -> list[dict[str, Any]]:
    pid = _pid()
    async with session() as s:
        rows = (
            await s.scalars(
                select(AnalyticsPost)
                .where(AnalyticsPost.profile_id == pid)
                .order_by(AnalyticsPost.posted_at)
            )
        ).all()
        # Keys match what PerformanceAdaptAgent reads, so it consumes these rows
        # unchanged.
        return [
            {
                "id": r.external_id or str(r.id),
                "title": r.title,
                "topic": r.topic,
                "platform": r.platform,
                "posted_at": r.posted_at.isoformat() if r.posted_at else None,
                "week": r.week,
                "views": r.views,
                "saves": r.saves,
                "avg_watch_pct": r.avg_watch_pct,
                "median_ratio": r.median_ratio,
                "synthetic": r.synthetic,
            }
            for r in rows
        ]


async def add_analytics_post(post: dict[str, Any]) -> str:
    pid = _pid()
    async with session() as s:
        row = AnalyticsPost(
            profile_id=pid,
            external_id=str(post.get("id", "")),
            title=str(post.get("title", "")),
            topic=str(post.get("topic", "")),
            platform=str(post.get("platform", "")),
            posted_at=_parse_dt(post["posted_at"]) if post.get("posted_at") else None,
            week=int(post.get("week") or 1),
            views=int(post.get("views") or 0),
            saves=int(post.get("saves") or 0),
            avg_watch_pct=_opt_float(post.get("avg_watch_pct")),
            median_ratio=_opt_float(post.get("median_ratio")),
            synthetic=bool(post.get("synthetic", False)),
        )
        s.add(row)
        await s.flush()
        return str(row.id)


def _opt_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def clear_profile_data() -> None:
    """Wipe this profile's pipeline rows. Used by the dev reset endpoint."""
    pid = _pid()
    async with session() as s:
        for model in (Opportunity, Artifact, CalendarEvent, EngagementItem, AnalyticsPost):
            await s.execute(delete(model).where(model.profile_id == pid))
