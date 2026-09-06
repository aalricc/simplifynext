"""CDR talks to tools via MCP :8085. HTTP to P1/P3 is fallback only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from cdr import agui_map
from shared.http_clients import client
from shared.rag import retrieve as rag_retrieve

load_dotenv()

MCP_URL = os.getenv("MCP_URL", "http://localhost:8085")
FINDER_URL = os.getenv("OPPORTUNITY_FINDER_URL", "http://localhost:8081")
PIPELINE_URL = os.getenv("PIPELINE_MANAGER_URL", "http://localhost:8082")
OUTBOX = Path(__file__).resolve().parents[1] / "demo" / "outbox" / "cdr"


async def mcp_call(name: str, arguments: dict[str, Any] | None = None) -> Any:
    payload = {"name": name, "arguments": arguments or {}}
    # Every MCP call funnels through here, so this is where the board's
    # "MCP tool calls" panel gets fed. run_id comes from the contextvar set in
    # service.execute_run - callers on this path do not carry graph state.
    _report(name, arguments or {}, server="mcp:8085")
    try:
        async with client(timeout=15.0) as c:
            r = await c.post(f"{MCP_URL}/mcp/call", json=payload)
            r.raise_for_status()
            data = r.json()
            if data.get("result") is not None:
                return data["result"]
    except Exception:
        pass
    _report(name, arguments or {}, server="fallback")
    return await _fallback(name, arguments or {})


def _report(name: str, arguments: dict[str, Any], server: str) -> None:
    from cdr.runtime import current_run, emit_agui

    run_id = current_run()
    if run_id:
        emit_agui(run_id, agui_map.mcp_call(name, arguments, server=server, run_id=run_id))


async def _fallback(name: str, arguments: dict[str, Any]) -> Any:
    if name == "retrieve_creator_memory":
        return await rag_retrieve(arguments.get("query", ""), k=4)
    if name == "find_opportunities":
        # No Maya seed fallback: handing one persona's opportunities to another
        # creator is a silent wrong answer. An empty list is honest, and the
        # caller reports the failed tool call on the trace.
        try:
            async with client(timeout=15.0) as c:
                r = await c.post(f"{FINDER_URL}/tools/find_opportunities", json=arguments)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"opportunities": [], "error": f"finder unreachable: {exc}"}
    if name == "persist_and_schedule":
        try:
            async with client(timeout=15.0) as c:
                r = await c.post(f"{PIPELINE_URL}/tools/persist_and_schedule", json=arguments)
                r.raise_for_status()
                return r.json()
        except Exception:
            run_id = arguments.get("run_id", "local")
            dest = OUTBOX / str(run_id)
            dest.mkdir(parents=True, exist_ok=True)
            import json

            (dest / "persist.json").write_text(json.dumps(arguments, indent=2, default=str))
            return {"ok": True, "id": arguments.get("opportunity_id"), "via": "outbox"}
    return None


async def find_opportunities(payload: dict[str, Any]) -> dict[str, Any]:
    data = await mcp_call("find_opportunities", payload)
    if isinstance(data, dict):
        return data
    return {"opportunities": []}


async def retrieve_creator_memory(query: str) -> list[dict[str, Any]]:
    data = await mcp_call("retrieve_creator_memory", {"query": query})
    return data if isinstance(data, list) else []


async def persist_and_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    data = await mcp_call("persist_and_schedule", payload)
    return data if isinstance(data, dict) else {"ok": False}
