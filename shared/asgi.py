"""One-line service setup: CORS plus tenant resolution.

Every FastAPI service calls `configure(app)` where it used to call
`add_cors(app)`, and inherits:

  * CORS locked to the UI origin instead of "*"
  * the tenant middleware, which puts `profile_id` in the ContextVar that
    shared/tenant.py hands to the database layer
  * a `/health` addition for database reachability (services opt in)
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.fixtures import reset_persona, set_persona
from shared.tenant import (
    PERSONA_HEADER,
    PROFILE_HEADER,
    TOKEN_HEADER,
    TenantError,
    reset_profile,
    set_profile,
    verify_token,
)


def allowed_origins() -> list[str]:
    """Origins permitted to call this service with credentials.

    `["*"]` with `allow_credentials=True` is rejected by browsers anyway, so
    the previous setting was not permissive -- it was broken *and* advertised
    an open door. Set CORS_ORIGINS to a comma-separated list to override.
    """
    raw = os.getenv("CORS_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    ui = os.getenv("UI_URL", "http://localhost:8000")
    # Vite dev server for the CopilotKit client.
    return [ui, "http://localhost:5173", "http://127.0.0.1:5173"]


def add_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            PROFILE_HEADER,
            TOKEN_HEADER,
            PERSONA_HEADER,
        ],
    )


def add_tenant_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _tenant(request: Request, call_next):
        profile_id = request.headers.get(PROFILE_HEADER, "")
        token = request.headers.get(TOKEN_HEADER, "")

        if profile_id:
            try:
                ok = verify_token(profile_id, token)
            except TenantError as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)
            if not ok:
                return JSONResponse(
                    {
                        "error": "invalid tenant token",
                        "detail": (
                            f"{PROFILE_HEADER} was present but {TOKEN_HEADER} did not "
                            "verify. Service-to-service calls must go through "
                            "shared.tenant.outbound_headers()."
                        ),
                    },
                    status_code=403,
                )

        # Unset rather than empty-string, so require_profile() raises with its
        # own message instead of querying for profile_id = ''.
        ctx = set_profile(profile_id)
        # Which demo persona's fixtures this request should see. Ignored unless
        # USE_FIXTURES=1, and it only picks between checked-in demo files.
        persona_ctx = set_persona(request.headers.get(PERSONA_HEADER, ""))
        try:
            return await call_next(request)
        finally:
            reset_persona(persona_ctx)
            reset_profile(ctx)


def add_tenant_error_handler(app: FastAPI) -> None:
    """Turn a missing tenant into a clean 401 instead of a 500.

    `require_profile()` raises deep in the database layer. Unhandled that is a
    stack trace and an opaque "Internal Server Error"; the honest answer is
    that the request never said whose data it wanted.
    """

    @app.exception_handler(TenantError)
    async def _handler(_: Request, exc: TenantError) -> JSONResponse:
        return JSONResponse({"error": "no_tenant", "detail": str(exc)}, status_code=401)


def configure(app: FastAPI, *, tenant: bool = True) -> None:
    add_cors(app)
    add_tenant_error_handler(app)
    if tenant:
        add_tenant_middleware(app)
