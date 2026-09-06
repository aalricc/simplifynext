"""Translate CDR's internal schemas into the board's AG-UI event contract.

The board (`ui_client/static/app.js` + `components.js`) renders a fixed set of
events, documented in `demo/fixtures/README.md`:

    CUSTOM/agent_trace | mcp_call | opportunities | pipeline | engagement | memory
    TOOL_CALL_START / _ARGS / _END   with a `render_*` name

Everything the live CDR agent produces has to arrive in exactly those shapes or
it renders nothing. This module is the only place that knows both vocabularies,
so P2 can keep its own schemas and P4 can keep its components.

Two vocabularies deliberately do not match and are mapped here:

* `shared.schemas.OpportunityStatus` is the pipeline enum (`researched`,
  `outreached`, `engaged`, `meeting`). The board's kanban columns use the
  demo vocabulary (`qualified`, `outreach_sent`, `replied`, `negotiating`),
  which the fixtures also speak. Mapping here keeps both working.
* Finder scores are 0-100; the board formats scores as 0-1.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

# --------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------

# RunEvent.status -> the three states the trace ticker colours on.
TRACE_STATUS = {
    "ok": "done",
    "done": "done",
    "fail": "fail",
    "running": "running",
    "awaiting_send": "running",
}

# OpportunityStatus -> the board's kanban columns.
PIPELINE_STATUS = {
    "new": "new",
    "researched": "qualified",
    "packaged": "packaged",
    "outreached": "outreach_sent",
    "engaged": "replied",
    "meeting": "negotiating",
    "won": "won",
    "lost": "lost",
}


def trace_status(status: str) -> str:
    return TRACE_STATUS.get(str(status), "done")


def board_status(status: str) -> str:
    s = str(status or "new")
    return PIPELINE_STATUS.get(s, s)


def normalize_score(value: Any) -> float | None:
    """Finder emits 0-100, the board formats 0-1. Accept either."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1.0:
        score = score / 100.0
    return max(0.0, min(1.0, score))


# --------------------------------------------------------------------------
# CUSTOM events
# --------------------------------------------------------------------------

def custom(name: str, value: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    ev: dict[str, Any] = {"type": "CUSTOM", "name": name, "value": value}
    if run_id:
        ev["runId"] = run_id
    return ev


def agent_trace(agent: str, pattern: str, summary: str, status: str = "ok",
                service: str = "cdr", run_id: str = "") -> dict[str, Any]:
    return custom("agent_trace", {
        "agent": agent,
        "pattern": pattern,
        "service": service,
        "status": trace_status(status),
        "summary": summary,
    }, run_id)


def mcp_call(tool: str, arguments: dict[str, Any] | None = None,
             server: str = "mcp:8085", run_id: str = "") -> dict[str, Any]:
    return custom("mcp_call", {
        "server": server,
        "tool": tool,
        "args_summary": _args_summary(arguments or {}),
    }, run_id)


def _args_summary(arguments: dict[str, Any], limit: int = 90) -> str:
    """One readable line. Long payloads (packages, briefs) collapse to a shape."""
    parts = []
    for key, value in arguments.items():
        if isinstance(value, (dict, list)):
            token = f"{key}={{…}}" if isinstance(value, dict) else f"{key}[{len(value)}]"
        else:
            token = f"{key}={value}"
        parts.append(token)
    text = " ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def opportunities(rows: list[dict[str, Any]], run_id: str = "") -> dict[str, Any]:
    return custom("opportunities", {
        "opportunities": [
            {
                "opportunity_id": r.get("id") or r.get("opportunity_id") or "",
                "type": r.get("type") or "trend",
                "title": r.get("title") or "",
                "score": normalize_score(r.get("score")),
                "status": board_status(r.get("status") or "new"),
                "rationale": r.get("why_now") or r.get("rationale") or "",
            }
            for r in rows
            if isinstance(r, dict)
        ]
    }, run_id)


def pipeline(updates: list[tuple[str, str]], run_id: str = "") -> dict[str, Any]:
    return custom("pipeline", {
        "updates": [
            {"opportunity_id": oid, "status": board_status(status)}
            for oid, status in updates
            if oid
        ]
    }, run_id)


# --------------------------------------------------------------------------
# TOOL_CALL events - generative UI cards
# --------------------------------------------------------------------------

def tool_call(name: str, args: dict[str, Any], run_id: str = "") -> list[dict[str, Any]]:
    """One artifact card, as the three protocol frames the board reassembles."""
    call_id = f"tc_{uuid4().hex[:8]}"
    base = {"toolCallId": call_id}
    if run_id:
        base["runId"] = run_id
    return [
        {"type": "TOOL_CALL_START", "toolCallName": name, **base},
        {"type": "TOOL_CALL_ARGS", "delta": json.dumps(args, ensure_ascii=False, default=str), **base},
        {"type": "TOOL_CALL_END", **base},
    ]


# --------------------------------------------------------------------------
# schema -> card args
# --------------------------------------------------------------------------

def research_brief_args(brief: dict[str, Any], opp: dict[str, Any]) -> dict[str, Any]:
    """shared.schemas.ResearchBrief -> render_research_brief."""
    # The card has two read columns and the brief has four findings, so pain
    # points ride with the audience read and presence with the competitor read.
    # recommended_slots stays empty: posting slots are not chosen until the
    # package exists, and the calendar card is where they belong.
    return {
        "angle": opp.get("why_now") or opp.get("title") or "",
        "audience_read": _lines(brief.get("audience_insight")) + _lines(brief.get("pain_points")),
        "competitor_read": _lines(brief.get("peer_moves")) + _lines(brief.get("platform_presence")),
        "sources": [
            {"label": _domain(url), "note": url}
            for url in (brief.get("evidence_urls") or [])
        ],
        "recommended_slots": [],
    }


def content_package_args(package: dict[str, Any], version: int = 1,
                         changes: list[str] | None = None) -> dict[str, Any]:
    """shared.schemas.ContentPackage -> render_content_package."""
    plan = [p for p in (package.get("week_plan") or []) if isinstance(p, dict)]
    hero = str(package.get("hero_script") or "")
    # This runs on model output that has been through a rewrite, so captions
    # can be a list or a bare string by the time it lands here.
    captions = _as_caption_map(package.get("captions"))
    caption = next(iter(captions.values()), "")
    first = plan[0] if plan and isinstance(plan[0], dict) else {}
    return {
        "version": version,
        "hook": first.get("hook") or "",
        "changes": list(changes or []),
        "duration_s": 60,
        "script": _beats(hero),
        "shot_list": [
            f"{p.get('format', 'talking-head')} — {p.get('hook', '')}"
            for p in plan
            if isinstance(p, dict)
        ],
        "caption": caption,
        "hashtags": re.findall(r"#\w+", " ".join(str(v) for v in captions.values())),
        "platform": first.get("platform") or "tiktok",
        "package_id": package.get("opportunity_id") or "",
        "status": "draft" if version == 1 else "revised",
    }


def qa_verdict_args(verdicts: list[dict[str, Any]], iteration: int,
                    max_iterations: int = 3) -> dict[str, Any]:
    """A round of shared.schemas.QAVerdict -> one render_qa_verdict card."""
    failed = any(str(v.get("verdict")) == "fail" for v in verdicts)
    issues: list[str] = []
    must_fix: list[str] = []
    for v in verdicts:
        issues.extend(v.get("issues") or [])
        must_fix.extend(v.get("must_fix") or [])
    return {
        "verdict": "fail" if failed else "pass",
        "iteration": iteration,
        "max_iterations": max_iterations,
        "critics": [
            {"agent": v.get("agent", ""), "verdict": v.get("verdict", "")}
            for v in verdicts
        ],
        "issues": issues,
        "must_fix": must_fix,
    }


def outreach_args(draft: dict[str, Any], opp: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """shared.schemas.OutreachDraft -> (component name, args). Channel picks the card."""
    channel = str(draft.get("channel") or "email")
    status = str(draft.get("status") or "drafted")
    if channel == "dm":
        return "render_dm_script", {
            "channel": "instagram dm",
            "to": draft.get("to") or "",
            "message": draft.get("body") or "",
            "status": status,
        }
    if channel == "call_script":
        body = str(draft.get("body") or "")
        points = _lines(body)
        return "render_call_script", {
            "opening": points[0] if points else body,
            "key_points": points[1:-1] if len(points) > 2 else points[1:],
            "objections": [],
            "close": points[-1] if len(points) > 1 else "",
        }
    return "render_outreach_email", {
        "to": draft.get("to") or "",
        # The opportunity title is the pitch, not the recipient - putting it in
        # the To: line read as nonsense. Let the card fall back to the address.
        "to_name": opp.get("brand") or opp.get("partner") or "",
        "from_name": "Maya Tan",
        "subject": draft.get("subject") or "",
        "body": draft.get("body") or "",
        "status": status,
    }


def calendar_args(package: dict[str, Any], timezone: str = "Asia/Singapore") -> dict[str, Any]:
    """ContentPackage.week_plan -> render_calendar_week."""
    monday = _next_monday()
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    slots: dict[str, list[dict[str, str]]] = {d: [] for d in days}
    for item in package.get("week_plan") or []:
        if not isinstance(item, dict):
            continue
        day, time = _parse_slot(str(item.get("posting_slot") or ""))
        slots.setdefault(day, []).append({
            "time": time,
            "title": item.get("hook") or "",
            "kind": item.get("platform") or "tiktok",
        })
    return {
        "week_of": monday.isoformat(),
        "timezone": timezone,
        "slots": [{"day": d, "items": slots.get(d, [])} for d in days],
    }


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _as_caption_map(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {f"caption_{i + 1}": str(v) for i, v in enumerate(raw)}
    return {"caption_1": str(raw)} if raw else {}


def _lines(value: Any) -> list[str]:
    """Model output is prose or a list. The cards want a list of bullets."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"[\n•]|(?<=[.!?])\s+", text)]
    return [p for p in parts if p]


def _beats(hero_script: str) -> list[dict[str, str]]:
    """Turn a flat script into timed beats so the card has something to show."""
    parts = _lines(hero_script)
    if not parts:
        return []
    step = max(1, 60 // len(parts))
    return [
        {"t": f"0:{min(59, i * step):02d}", "beat": part}
        for i, part in enumerate(parts)
    ]


def _domain(url: str) -> str:
    match = re.search(r"https?://([^/]+)", str(url))
    return match.group(1) if match else str(url)[:40]


def _next_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


_DAYS = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
         "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def _parse_slot(slot: str) -> tuple[str, str]:
    """'Fri 19:30 SGT' -> ('Fri', '19:30'). Unknown shapes land on Mon."""
    day = "Mon"
    for key, label in _DAYS.items():
        if key in slot.lower():
            day = label
            break
    match = re.search(r"(\d{1,2}:\d{2})", slot)
    return day, match.group(1) if match else "19:00"
