"""Who this request belongs to, carried without touching every signature.

WHY A CONTEXTVAR
----------------
`profile_id` has to survive five hops:

    browser -> ui_client -> cdr -> mcp -> pipeline_manager -> database

Passing it as an argument would mean editing all fourteen MCP tool signatures
(`mcp/tools/__init__.py:dispatch` rejects unexpected arguments), every agent
call site, and the schema generator behind `GET /mcp/tools`.

So instead it rides in a ContextVar, exactly as `cdr/runtime.py` already does
for `run_id`. `asyncio.gather` copies the context, so the parallel research
fan-out inherits it for free.

WHY THE HEADER IS SIGNED
------------------------
The backend services have no authentication of their own. If a bare
`X-CreatorLoop-Profile` header decided tenancy, anyone who could reach
pipeline_manager could read any creator's data by naming a different profile.

So ui_client -- the only service that validates a session cookie -- mints an
HMAC over the profile id, and every other service verifies it before trusting
the header. This is defence in depth, not the primary control: the primary
control is that only ui_client publishes a port (see compose.yaml).
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import os
import time

PROFILE_HEADER = "X-CreatorLoop-Profile"
TOKEN_HEADER = "X-CreatorLoop-Token"
# Which demo persona's canned data to serve. Only read when USE_FIXTURES=1, and
# unsigned on purpose: it selects between checked-in demo files, so the worst a
# forged value can do is show you the wrong demo. It rides here because a
# fixture run spans all six services and `search_web` gets no profile argument.
PERSONA_HEADER = "X-CreatorLoop-Persona"

# How long a minted internal token stays valid. Long enough for a full campaign
# run (the README says 3-5 minutes), short enough that a leaked header from a
# log file is useless later.
TOKEN_TTL_SECONDS = 3600

_profile: contextvars.ContextVar[str] = contextvars.ContextVar("creatorloop_profile", default="")


class TenantError(RuntimeError):
    """No profile in context, or a header that failed verification."""


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def set_profile(profile_id: str) -> contextvars.Token:
    return _profile.set(str(profile_id or ""))


def reset_profile(token: contextvars.Token) -> None:
    _profile.reset(token)


def current_profile() -> str:
    """The profile this request belongs to, or '' if none is set."""
    return _profile.get()


def require_profile() -> str:
    """Same, but raises rather than silently reading or writing the wrong rows.

    Every database helper goes through this. A missing tenant must be a loud
    failure -- the quiet alternative is a cross-tenant read.
    """
    pid = _profile.get()
    if not pid:
        raise TenantError(
            "No creator profile in context. The request reached this service "
            f"without a valid {PROFILE_HEADER} header."
        )
    return pid


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------


def signing_key() -> bytes:
    key = os.getenv("INTERNAL_SIGNING_KEY", "")
    if not key:
        # Dev default, deliberately obvious. Services refuse to *verify* with
        # this key when CREATORLOOP_ENV=production (see verify_token).
        key = "dev-insecure-internal-key"
    return key.encode()


def mint_token(profile_id: str, *, now: int | None = None) -> str:
    """Sign a profile id. Returned as `<expiry>.<hex mac>`."""
    exp = int(now if now is not None else time.time()) + TOKEN_TTL_SECONDS
    payload = f"{profile_id}.{exp}"
    mac = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{mac}"


def verify_token(profile_id: str, token: str, *, now: int | None = None) -> bool:
    if os.getenv("CREATORLOOP_ENV", "dev") == "production" and not os.getenv(
        "INTERNAL_SIGNING_KEY"
    ):
        raise TenantError(
            "INTERNAL_SIGNING_KEY is unset in production. Refusing to verify "
            "internal tokens with the shared dev key."
        )
    if not token or "." not in token:
        return False
    exp_raw, _, mac = token.partition(".")
    try:
        exp = int(exp_raw)
    except ValueError:
        return False
    if exp < int(now if now is not None else time.time()):
        return False
    expected = hmac.new(
        signing_key(), f"{profile_id}.{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, mac)


def outbound_headers(profile_id: str | None = None) -> dict[str, str]:
    """Headers to attach to any service-to-service call.

    `shared/http_clients.py` and `cdr/mcp_client.py` call this, so adding a new
    outbound call site does not mean remembering to propagate tenancy.
    """
    from shared.fixtures import current_persona
    from shared.flags import use_fixtures

    pid = profile_id or current_profile()
    headers: dict[str, str] = {}
    if pid:
        headers[PROFILE_HEADER] = pid
        headers[TOKEN_HEADER] = mint_token(pid)
    if use_fixtures():
        headers[PERSONA_HEADER] = current_persona()
    return headers
