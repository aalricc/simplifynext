from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from cdr import agui_map, run_store
from cdr.graph import run_campaign
from cdr.runtime import (
    emit_agui,
    emit_custom,
    emit_error,
    finish,
    get_run,
    new_run,
    set_current_run,
)
from shared.fixtures import use_persona_of
from shared.tenant import current_profile, require_profile, set_profile

OUTBOX = Path(__file__).resolve().parents[1] / "demo" / "outbox" / "cdr"


class ProfileMissing(RuntimeError):
    """No creator profile on the request. Never fall back to a demo persona."""


async def profile_from(body: dict) -> dict:
    """The creator this run is for.

    ui_client sends the full profile alongside the signed tenant header, so the
    common path needs no extra query. If only the header arrived, load it.

    There is deliberately no `demo/maya/profile.json` fallback any more: a run
    that cannot say whose it is must fail loudly rather than produce a campaign
    for the wrong person.
    """
    if isinstance(body.get("profile"), dict) and body["profile"].get("id"):
        return body["profile"]

    profile_id = str(body.get("profile_id") or current_profile() or "")
    if not profile_id:
        raise ProfileMissing(
            "No creator profile on this run. The request reached the CDR agent "
            "without a verified X-CreatorLoop-Profile header."
        )

    import uuid as _uuid

    from shared.db import session
    from shared.models import CreatorProfile

    try:
        pid = _uuid.UUID(profile_id)
    except ValueError as exc:
        raise ProfileMissing(f"profile id {profile_id!r} is not a UUID") from exc

    async with session() as s:
        row = await s.get(CreatorProfile, pid)
    if row is None:
        raise ProfileMissing(f"No creator profile with id {profile_id}")
    return row.to_profile_dict()


def opportunities_from(body: dict) -> list:
    """Only what the caller supplied.

    The Maya seed file used to backfill this. It cannot any more -- those
    opportunities are one persona's, and handing them to another creator is
    exactly the silent-wrong-answer case. An empty list means the graph's
    `load_opportunities` node calls the Finder for real.
    """
    if body.get("opportunities"):
        return list(body["opportunities"])
    return []


async def start_run(body: dict, run_id: str | None = None) -> str:
    run_id = run_id or str(uuid4())[:8]
    profile = await profile_from(body)
    opps = opportunities_from(body)
    profile_id = str(profile.get("id") or require_profile())
    new_run(run_id, profile_id, [o.get("id", "") for o in opps if isinstance(o, dict)])
    await run_store.start_run(run_id, profile_id, week=int(body.get("week") or 1))
    return run_id


async def execute_run(run_id: str, body: dict) -> None:
    set_current_run(run_id)
    try:
        profile = await profile_from(body)
    except ProfileMissing as exc:
        emit_error(run_id, str(exc))
        finish(run_id, {"error": str(exc)})
        return

    # Pin the tenant for the whole run. This task is detached from the request
    # that started it (`asyncio.create_task`) and outlives it by minutes, so it
    # must not depend on context inherited from that request -- without this,
    # every outbound MCP and Pipeline call goes out unauthenticated and the
    # run's results are silently dropped.
    set_profile(str(profile.get("id", "")))
    # Pick the demo persona this creator maps onto, so a fixture run tells
    # their story rather than Maya's. No effect when USE_FIXTURES=0.
    use_persona_of(profile)

    opps = opportunities_from(body)
    emit_agui(
        run_id,
        {
            "type": "RUN_STARTED",
            "runId": run_id,
            "threadId": body.get("threadId") or str(profile.get("id", "")),
        },
    )
    if opps:
        emit_custom(run_id, "opportunities", agui_map.opportunities(opps, run_id)["value"])

    state = {
        "run_id": run_id,
        "profile": profile,
        "opportunities": opps,
        "pause_before_send": os.getenv("PAUSE_BEFORE_SEND", "0") in ("1", "true", "True")
        or bool(body.get("pause_before_send")),
        "packages": [],
        "outreach": [],
        "qa": [],
    }
    try:
        result = await run_campaign(state)
        dest = OUTBOX / run_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "result.json").write_text(json.dumps(result, indent=2, default=str))
        rec = get_run(run_id) or {}
        rec["packages"] = result.get("packages") or []
        rec["outreach"] = result.get("outreach") or []
        rec["qa"] = result.get("qa") or []
    except Exception as exc:
        rec = get_run(run_id) or {}
        rec["status"] = "error"
        rec["error"] = str(exc)
        # Say so on the board, then close the stream - a silent hang is worse
        # than a visible failure mid-run.
        emit_error(run_id, str(exc))
        finish(run_id, {"error": str(exc)})
