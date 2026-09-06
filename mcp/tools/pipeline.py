"""P3 pipeline / engagement tools for the MCP server.

Pipeline CRUD goes through Pipeline Manager HTTP (single writer). Local tools
handle inbox fixtures, RAG memory retrieval, and demo email outbox.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mcp.tools import tool
from shared.http_clients import PIPELINE_MANAGER_URL, client
from shared.rag import rank

OUTBOX_MAIL = Path(__file__).resolve().parents[2] / "demo" / "outbox" / "mail"


async def _pipeline_call(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    # shared.http_clients.client() attaches this request's signed tenant
    # headers, so Pipeline Manager scopes the query to the right creator.
    async with client(timeout=15.0) as c:
        r = await c.request(method, f"{PIPELINE_MANAGER_URL}{path}", **kwargs)
        r.raise_for_status()
        return r.json()


@tool(
    "retrieve_creator_memory",
    owner="P2/P3",
    kind="rag",
    description="Retrieve creator memory and past-post context (multi-agent RAG).",
)
async def retrieve_creator_memory(query: str, k: int = 4) -> list[dict[str, Any]]:
    """Keyword RAG over this creator's `rag_documents`. Returns docs for CDR."""
    data = await _pipeline_call("GET", "/pipeline/rag")
    return rank(data.get("documents") or [], query, k)


@tool(
    "read_engagement_inbox",
    owner="P3",
    description="Read inbound replies for the creator (week-2 adapt loop).",
)
async def read_engagement_inbox(unread_only: bool = False) -> dict[str, Any]:
    """Inbound replies for the creator in context, from `engagement_items`."""
    data = await _pipeline_call(
        "GET", "/pipeline/engagement", params={"unread_only": str(unread_only).lower()}
    )
    return {"items": data.get("items") or [], "mode": "live"}


@tool(
    "save_opportunity",
    owner="P3",
    description="Upsert an opportunity row into the pipeline SQLite store.",
)
async def save_opportunity(**kwargs: Any) -> dict[str, Any]:
    return await _pipeline_call("POST", "/pipeline/upsert", json=kwargs)


@tool(
    "get_opportunity",
    owner="P3",
    description="Fetch one opportunity by id from the pipeline store.",
)
async def get_opportunity(id: str = "", opportunity_id: str = "") -> dict[str, Any]:
    oid = str(id or opportunity_id or "")
    return await _pipeline_call("GET", f"/pipeline/opportunities/{oid}")


@tool(
    "update_status",
    owner="P3",
    description="Transition an opportunity status via StatusTrackerAgent.",
)
async def update_status(
    id: str = "",
    opportunity_id: str = "",
    status: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    oid = str(id or opportunity_id or "")
    return await _pipeline_call(
        "POST",
        "/tools/update_status",
        json={"opportunity_id": oid, "status": status, **kwargs},
    )


@tool(
    "persist_and_schedule",
    owner="P3",
    description="Persist an opportunity/package and schedule its calendar slots.",
)
async def persist_and_schedule(**kwargs: Any) -> dict[str, Any]:
    return await _pipeline_call("POST", "/tools/persist_and_schedule", json=kwargs)


@tool(
    "save_calendar_event",
    owner="P3",
    description="Save a post, follow-up, or meeting to the content calendar.",
)
async def save_calendar_event(**kwargs: Any) -> dict[str, Any]:
    return await _pipeline_call("POST", "/pipeline/calendar", json=kwargs)


@tool(
    "send_email",
    owner="P3",
    description="Write an outreach email to demo/outbox/mail (optional SMTP).",
)
async def send_email(
    to: str = "",
    subject: str = "",
    body: str = "",
    opportunity_id: str = "draft",
    **kwargs: Any,
) -> dict[str, Any]:
    to_addr = str(to or kwargs.get("to") or "unknown@local")
    subj = str(subject or kwargs.get("subject") or "")
    text = str(body or kwargs.get("body") or "")
    oid = str(opportunity_id or kwargs.get("opportunity_id") or "draft")
    from_addr = os.getenv("FROM_EMAIL", "maya@creatorloop.local")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTBOX_MAIL.mkdir(parents=True, exist_ok=True)
    eml_path = OUTBOX_MAIL / f"{oid}-{ts}.eml"
    eml_path.write_text(
        f"From: {from_addr}\nTo: {to_addr}\nSubject: {subj}\n\n{text}\n",
        encoding="utf-8",
    )

    status = "sent_mock"
    smtp_host = os.getenv("SMTP_HOST")
    if smtp_host:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg["Subject"] = subj
            msg.set_content(text)
            with smtplib.SMTP(smtp_host) as smtp:
                user = os.getenv("SMTP_USER")
                password = os.getenv("SMTP_PASSWORD")
                if user and password:
                    smtp.starttls()
                    smtp.login(user, password)
                smtp.send_message(msg)
            status = "sent"
        except Exception as exc:  # best effort in demo — .eml is already on disk
            status = f"smtp_failed: {exc}"

    return {"status": status, "eml_path": str(eml_path), "to": to_addr}


@tool(
    "write_memory",
    owner="P3",
    description="Persist week-2 performance memory (wins/losses/next_bias).",
)
async def write_memory(
    wins: list | None = None,
    losses: list | None = None,
    next_bias: list | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    memory = {
        "wins": list(wins if wins is not None else kwargs.get("wins") or []),
        "losses": list(losses if losses is not None else kwargs.get("losses") or []),
        "next_bias": list(next_bias if next_bias is not None else kwargs.get("next_bias") or []),
    }
    # Allow callers to pass a full memory dict as kwargs-only payload.
    if not any(memory.values()) and isinstance(kwargs.get("memory"), dict):
        memory = {
            "wins": kwargs["memory"].get("wins", []),
            "losses": kwargs["memory"].get("losses", []),
            "next_bias": kwargs["memory"].get("next_bias", []),
        }
    return await _pipeline_call("POST", "/pipeline/memory", json=memory)
