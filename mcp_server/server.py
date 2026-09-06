"""MCP tool server (kick-off stack: Model Context Protocol).

Thin dispatcher over the registry in `mcp_server/tools/`. Tool implementations live in:

  * `mcp_server/tools/search.py`   -- P1: search_web, search_local_places, fetch_url, find_opportunities
  * `mcp_server/tools/pipeline.py` -- P3: persist, calendar, inbox, memory, CRUD, email

Agents call these through `shared/mcp_client.py`, never with ad-hoc HTTP.

Transport today is a JSON shim (`POST /mcp/call`) because P2 and P3 already
build against it. Tools are plain async functions, so a real FastMCP server can
expose the same registry later without touching any tool code.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from mcp_server.tools import ToolError, dispatch, list_specs
from shared.cors import add_cors
from shared.flags import use_fixtures

app = FastAPI(title="CreatorLoop MCP", version="0.2.0")
add_cors(app)


class ToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


@app.get("/health")
def health() -> dict:
    return {
        "service": "mcp",
        "status": "ok",
        "protocol": "mcp",
        "tools": len(list_specs()),
        "fixtures": use_fixtures(),
    }


@app.get("/mcp/tools")
def list_tools() -> dict:
    """Self-describing tool list, including input schemas for P2's agents."""
    return {"tools": list_specs()}


@app.post("/mcp/call")
async def call_tool(req: ToolCall) -> dict:
    """Invoke one tool by name. Errors come back as data, never a 500 --
    a tool failure must not take down an agent run mid-demo."""
    try:
        result = await dispatch(req.name, req.arguments)
    except ToolError as exc:
        return {"name": req.name, "result": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"name": req.name, "result": None, "error": f"{type(exc).__name__}: {exc}"}

    return {"name": req.name, "result": result, "error": None}
