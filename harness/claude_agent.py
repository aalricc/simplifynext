"""Claude Agent SDK path — the optional specialist critic.

Used by `cdr/agents/fact_checker.py`. Returns None unless ANTHROPIC_API_KEY is
set, so Groq and fixture runs are unaffected.

WHY THIS WAS DEAD CODE
----------------------
The MCP tool server used to live in a top-level package called `mcp/`, which
shadowed the PyPI `mcp` package that claude_agent_sdk imports. From the repo
root `import claude_agent_sdk` raised ModuleNotFoundError, the bare `except`
below swallowed it, and the specialist silently never ran — while STACK.md
listed the Claude Agent SDK as part of the stack. The package is now
`mcp_server/`, and `sdk_status()` exists so the failure can never be silent
again: `GET /health` on the CDR reports it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


class SpecialistUnavailable(RuntimeError):
    """The SDK is not usable. Carries why, so /health can say so."""


def sdk_status() -> dict[str, Any]:
    """Whether the specialist can run, and if not, precisely why.

    Surfaced on the CDR's /health so a dead integration shows up before a demo
    rather than during one.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"available": False, "reason": "ANTHROPIC_API_KEY not set"}
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"available": True, "reason": None}


async def run_claude_specialist(prompt: str) -> dict[str, Any] | None:
    """One Claude Agent SDK call returning JSON, or None if it cannot run."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None

    try:
        from claude_agent_sdk import query
    except Exception as exc:
        # An import failure here means the environment is broken, not that the
        # specialist was declined. Say so once rather than silently degrading.
        print(f"[claude_agent] SDK present but unusable: {type(exc).__name__}: {exc}")
        return None

    try:
        text = ""
        async for msg in query(prompt=prompt):
            text += str(getattr(msg, "text", msg) or "")
    except Exception as exc:
        print(f"[claude_agent] specialist call failed: {type(exc).__name__}: {exc}")
        return None

    raw = text.strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
