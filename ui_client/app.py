"""CreatorLoop UI Client -- the only public service.

This is the real application. `ui_client/server.py` is the stdlib-only,
no-pip-install fixture demo and stays for that purpose alone; it does not read
the database and cannot log anyone in.

Responsibilities:

  * accounts and sessions (ui_client/auth.py)
  * onboarding -- the form that replaces demo/maya/profile.json
  * the board's read APIs, scoped to the signed-in profile
  * POST /ag-ui, proxied to the CDR agent with a *signed* tenant header

Everything behind this service trusts that header, so this is the one place a
human is authenticated. See shared/tenant.py for why it is signed.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline_manager import db as pipeline_db
from shared.asgi import configure
from shared.db import dispose, healthcheck, is_sqlite
from shared.models import CreatorProfile, User
from shared.seed import seed_new_profile
from shared.tenant import outbound_headers, reset_profile, set_profile
from ui_client import auth

load_dotenv()

STATIC = Path(__file__).resolve().parent / "static"
CDR_AGUI_URL = os.getenv("CDR_AGUI_URL", "http://localhost:8084/ag-ui")
LIVE_TIMEOUT = float(os.getenv("LIVE_TIMEOUT", "300"))
SECURE_COOKIES = os.getenv("CREATORLOOP_ENV", "dev") == "production"

app = FastAPI(title="CreatorLoop UI", version="1.0.0")
# tenant=False: this service resolves the tenant from the session cookie, not
# from an inbound header. Accepting a header here would let a browser name any
# profile it liked.
configure(app, tenant=False)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
async def _startup() -> None:
    # SQLite has no migration story worth having; a real server goes through
    # `alembic upgrade head` (the compose `migrate` service does this).
    if is_sqlite():
        from shared.db import create_all

        await create_all()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await dispose()


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


async def current_user(request: Request) -> User:
    user = await auth.resolve_session(request.cookies.get(auth.COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


async def active_profile(
    request: Request, user: User = Depends(current_user)
) -> CreatorProfile:
    """The profile this request acts on.

    Always re-checked against `user_id`: a profile id from a cookie or query
    string is untrusted input, and this is the check that stops one account
    naming another account's profile.
    """
    requested = request.query_params.get("profile_id") or request.cookies.get(
        "creatorloop_profile"
    )
    if requested:
        profile = await auth.get_profile(user.id, requested)
        if profile is not None:
            return profile

    profiles = await auth.list_profiles(user.id)
    if not profiles:
        raise HTTPException(status_code=409, detail="onboarding_required")
    return profiles[0]


@app.exception_handler(auth.AuthError)
async def _auth_error(_: Request, exc: auth.AuthError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=auth.SESSION_DAYS * 86400,
        path="/",
    )


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


@app.get("/")
async def landing() -> FileResponse:
    return FileResponse(STATIC / "landing.html")


@app.get("/signin")
@app.get("/signup")
async def signin_page() -> FileResponse:
    return FileResponse(STATIC / "auth.html")


@app.get("/onboarding")
async def onboarding_page() -> FileResponse:
    return FileResponse(STATIC / "onboarding.html")


@app.get("/board")
async def board() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"service": "ui_client", "status": "ok", **(await healthcheck())}


# ---------------------------------------------------------------------------
# auth routes
# ---------------------------------------------------------------------------


@app.post("/auth/signup")
async def signup(request: Request) -> JSONResponse:
    body = await request.json()
    user = await auth.signup(
        body.get("email", ""), body.get("password", ""), body.get("display_name", "")
    )
    token = await auth.create_session(user.id, request.headers.get("user-agent", ""))
    payload = {
        "user": {"id": str(user.id), "email": user.email, "name": user.display_name},
        "next": "/onboarding",
    }
    response = JSONResponse(payload, status_code=201)
    _set_session_cookie(response, token)
    return response


@app.post("/auth/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    user = await auth.login(body.get("email", ""), body.get("password", ""))
    token = await auth.create_session(user.id, request.headers.get("user-agent", ""))
    profiles = await auth.list_profiles(user.id)
    response = JSONResponse(
        {
            "user": {"id": str(user.id), "email": user.email, "name": user.display_name},
            "next": "/board" if profiles else "/onboarding",
        }
    )
    _set_session_cookie(response, token)
    if profiles:
        response.set_cookie(
            "creatorloop_profile", str(profiles[0].id), samesite="lax", path="/"
        )
    return response


@app.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    await auth.revoke_session(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"ok": True, "next": "/"})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    response.delete_cookie("creatorloop_profile", path="/")
    return response


@app.get("/auth/me")
async def me(user: User = Depends(current_user)) -> dict:
    profiles = await auth.list_profiles(user.id)
    return {
        "user": {"id": str(user.id), "email": user.email, "name": user.display_name},
        "profiles": [
            {
                "id": str(p.id),
                "handle": p.handle,
                "name": p.display_name,
                "city": p.city,
                "niche": p.niche,
            }
            for p in profiles
        ],
        "onboarding_required": not profiles,
    }


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


@app.post("/api/profiles")
async def create_profile(request: Request, user: User = Depends(current_user)) -> JSONResponse:
    """Onboarding submit. This is what replaces demo/maya/profile.json."""
    form = await request.json()
    profile = await auth.create_profile(user.id, form)

    # Starter memory row + a small RAG corpus from the answers, so the first
    # campaign has something to retrieve instead of an empty result.
    ctx = set_profile(str(profile.id))
    try:
        seeded = await seed_new_profile(profile.to_profile_dict())
    finally:
        reset_profile(ctx)

    response = JSONResponse(
        {"profile": profile.to_profile_dict(), "seeded": seeded, "next": "/board"},
        status_code=201,
    )
    response.set_cookie("creatorloop_profile", str(profile.id), samesite="lax", path="/")
    return response


@app.get("/api/profile")
async def get_profile(profile: CreatorProfile = Depends(active_profile)) -> dict:
    """Was `FileResponse(demo/maya/profile.json)`. Now the signed-in creator."""
    return profile.to_profile_dict()


@app.post("/api/profile")
async def patch_profile(
    request: Request,
    user: User = Depends(current_user),
    profile: CreatorProfile = Depends(active_profile),
) -> dict:
    updated = await auth.update_profile(user.id, str(profile.id), await request.json())
    return updated.to_profile_dict()


@app.post("/api/profiles/{profile_id}/select")
async def select_profile(
    profile_id: str, user: User = Depends(current_user)
) -> JSONResponse:
    profile = await auth.get_profile(user.id, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    response = JSONResponse({"profile": profile.to_profile_dict()})
    response.set_cookie("creatorloop_profile", str(profile.id), samesite="lax", path="/")
    return response


# ---------------------------------------------------------------------------
# board data -- all tenant-scoped
# ---------------------------------------------------------------------------


def _scoped(profile: CreatorProfile):
    """Run a pipeline_db call inside this profile's tenant context."""

    class _Ctx:
        async def __aenter__(self):
            self.token = set_profile(str(profile.id))
            return self

        async def __aexit__(self, *exc):
            reset_profile(self.token)
            return False

    return _Ctx()


@app.get("/api/config")
async def config(request: Request) -> dict:
    user = await auth.resolve_session(request.cookies.get(auth.COOKIE_NAME))
    profiles = await auth.list_profiles(user.id) if user else []
    return {
        "mode": "live",
        "cdr_agui_url": CDR_AGUI_URL,
        "pause_before_send": os.getenv("PAUSE_BEFORE_SEND", "0") not in ("0", "false"),
        "authenticated": user is not None,
        "onboarding_required": bool(user) and not profiles,
        "profile_id": str(profiles[0].id) if profiles else None,
    }


@app.get("/api/opportunities")
async def opportunities(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return {"opportunities": await pipeline_db.list_opportunities()}


@app.get("/api/calendar")
async def calendar(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return {"events": await pipeline_db.list_calendar_events()}


@app.get("/api/memory")
async def memory(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return await pipeline_db.get_memory() or {
            "wins": [],
            "losses": [],
            "next_bias": [],
        }


@app.get("/api/inbox")
async def inbox(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return {"items": await pipeline_db.list_engagement_items()}


@app.get("/api/artifacts")
async def artifacts(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return {"artifacts": await pipeline_db.list_artifacts()}


@app.get("/api/rag_corpus")
async def rag_corpus(profile: CreatorProfile = Depends(active_profile)) -> dict:
    async with _scoped(profile):
        return {"documents": await pipeline_db.list_rag_documents()}


@app.get("/api/runs")
async def runs(profile: CreatorProfile = Depends(active_profile)) -> dict:
    from cdr import run_store

    return {"runs": await run_store.list_runs(str(profile.id))}


@app.get("/api/runs/{run_id}/events")
async def run_events(
    run_id: str, profile: CreatorProfile = Depends(active_profile)
) -> dict:
    """Replay a finished run. Same frames the live stream sent.

    This is what `demo/fixtures/*.jsonl` was standing in for.
    """
    from cdr import run_store

    events = await run_store.load_agui_events(run_id, profile_id=str(profile.id))
    if events is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"run_id": run_id, "events": events}


# ---------------------------------------------------------------------------
# AG-UI
# ---------------------------------------------------------------------------


@app.post("/ag-ui")
async def ag_ui(request: Request, profile: CreatorProfile = Depends(active_profile)):
    """Proxy the run to the CDR agent, carrying the signed tenant header.

    This is the hand-off: the cookie was checked above, and from here on the
    backend services trust the header this mints. Nothing downstream ever sees
    the session cookie.
    """
    body: dict[str, Any] = await request.json()
    body = {
        **body,
        "profile_id": str(profile.id),
        "profile": profile.to_profile_dict(),
        "threadId": body.get("threadId") or str(profile.id),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **outbound_headers(str(profile.id)),
    }

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=LIVE_TIMEOUT) as client:
                async with client.stream(
                    "POST", CDR_AGUI_URL, json=body, headers=headers
                ) as upstream:
                    if upstream.status_code >= 400:
                        detail = (await upstream.aread()).decode("utf-8", "replace")[:400]
                        yield _frame(
                            {
                                "type": "RUN_ERROR",
                                "runId": body.get("runId", ""),
                                "message": f"CDR returned {upstream.status_code}: {detail}",
                            }
                        )
                        return
                    async for line in upstream.aiter_lines():
                        if line.startswith("data:"):
                            yield f"{line}\n\n"
        except (httpx.HTTPError, OSError) as exc:
            # Say so on the board rather than leaving it spinning on a dead
            # stream. There is no fixture fallback any more: serving another
            # creator's canned campaign as if it were this one's is worse than
            # a visible failure.
            yield _frame(
                {
                    "type": "CUSTOM",
                    "name": "agent_trace",
                    "value": {
                        "agent": "RunSupervisor",
                        "pattern": "custom",
                        "service": "ui",
                        "status": "fail",
                        "summary": f"Could not reach the CDR agent at {CDR_AGUI_URL}: {exc}",
                    },
                }
            )
            yield _frame({"type": "RUN_ERROR", "runId": body.get("runId", ""), "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/stop")
async def stop(request: Request, profile: CreatorProfile = Depends(active_profile)) -> dict:
    body = await request.json()
    run_id = body.get("runId")
    if not run_id:
        return {"stopped": None}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{os.getenv('CDR_URL', 'http://localhost:8084')}/cdr/runs/{run_id}/stop",
                headers=outbound_headers(str(profile.id)),
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        return {"stopped": run_id, "warning": str(exc)}
    return {"stopped": run_id}


# ---------------------------------------------------------------------------
# dev helpers
# ---------------------------------------------------------------------------


@app.post("/api/dev/simulate_week")
async def simulate_week(
    request: Request, profile: CreatorProfile = Depends(active_profile)
) -> dict:
    """Generate a week of inbound replies and analytics for this profile.

    Replaces `POST /engagement/replay_maya_week2`, which replayed one hardcoded
    file for one persona. The adapt loop still has to be demonstrable without
    waiting a real week, and a new user's week 2 has to come from somewhere.
    """
    if os.getenv("CREATORLOOP_ENV", "dev") == "production":
        raise HTTPException(status_code=404, detail="Not available.")

    from shared.simulate import simulate_week_for_profile

    async with _scoped(profile):
        result = await simulate_week_for_profile(profile.to_profile_dict())

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{os.getenv('ENGAGEMENT_LISTENER_URL', 'http://localhost:8083')}"
            "/engagement/process_week",
            json={},
            headers=outbound_headers(str(profile.id)),
        )
        r.raise_for_status()
        processed = r.json()

    return {"ok": True, "generated": result, "processed": processed}
