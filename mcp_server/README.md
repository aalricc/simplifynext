# MCP (P1 + P3)

Port **8085**. Model Context Protocol: agents call tools here instead of ad-hoc APIs.

## Layout

```
mcp/
  server.py          thin dispatcher — rarely edited, avoid conflicts here
  tools/
    __init__.py      registry + @tool decorator + dispatch()
    search.py        P1: search_web, search_local_places, fetch_url, find_opportunities
    pipeline.py      P3: persist_and_schedule, save_calendar_event,
                         read_engagement_inbox, retrieve_creator_memory,
                         save_opportunity, get_opportunity, update_status,
                         send_email, write_memory
```

**P1 edits `search.py`, P3 edits `pipeline.py`.** Nobody edits the same file,
so no merge conflicts. `server.py` only dispatches.

## Adding a tool

```python
from mcp_server.tools import tool

@tool("my_tool", owner="P3", description="What it does")
async def my_tool(thing: str, limit: int = 5) -> dict:
    return {"result": ...}
```

The decorator derives an input schema from the signature, so `GET /mcp/tools`
stays self-describing for P2's agents. Tools are plain async functions —
unit-testable without HTTP, and reusable by a real FastMCP server later.

## Calling tools

Never hand-roll httpx. Use [`shared/mcp_client.py`](../shared/mcp_client.py):

```python
from shared.mcp_client import search_web, call_tool

hits = await search_web("laksa trend singapore", limit=5)
mem  = await call_tool("retrieve_creator_memory", {"query": "dessert"})
```

## Routes

- `GET /health` — status, tool count, fixture mode
- `GET /mcp/tools` — name, owner, description, input schema
- `POST /mcp/call` — `{"name": ..., "arguments": {...}}`

Tool failures return `{"result": null, "error": "..."}` with HTTP 200. A broken
tool must never 500 an agent run mid-demo.

## Status

| Tool | Owner | State |
|---|---|---|
| `search_web` | P1 | ✅ fixture + Tavily adapter |
| `search_local_places` | P1 | ✅ filters city / category / has_short_form |
| `fetch_url` | P1 | ✅ live fetch + fixture intercept |
| `find_opportunities` | P1 | ✅ runs Opportunity Finder pipeline |
| `retrieve_creator_memory` | P2/P3 | ✅ keyword RAG (swap for embeddings) |
| `read_engagement_inbox` | P3 | ✅ fixture |
| `save_opportunity` | P3 | ✅ SQLite via pipeline_manager.db |
| `get_opportunity` | P3 | ✅ SQLite via pipeline_manager.db |
| `update_status` | P3 | ✅ StatusTrackerAgent |
| `persist_and_schedule` | P3 | ✅ Pipeline Manager graph |
| `save_calendar_event` | P3 | ✅ SQLite via pipeline_manager.db |
| `send_email` | P3 | ✅ writes demo/outbox/mail |
| `write_memory` | P3 | ✅ SQLite + memory.json |

## Fixtures vs live

`USE_FIXTURES=1` (default) serves [`demo/maya/`](../demo/maya/). Live paths
activate only when `USE_FIXTURES=0` **and** the relevant key is set
(`TAVILY_API_KEY` for `search_web`). Return shapes are identical either way, so
day-5 switchover is a one-line `.env` change.

Seed `evidence_urls` use the unresolvable `example.local` domain — `fetch_url`
serves those from [`demo/maya/search_web.json`](../demo/maya/search_web.json) so
P2's FactChecker works offline.

## Later: real FastMCP

Transport today is a JSON shim because P2/P3 already build against `/mcp/call`.
The `mcp` SDK is installed; a FastMCP server can wrap the same `REGISTRY` and
run alongside the shim without changing any tool code.

## Tools (owners)

P1: `search_web`, `search_local_places`, `fetch_url`, `find_opportunities`

P3: `retrieve_creator_memory`, `save_opportunity`, `get_opportunity`, `update_status`, `persist_and_schedule`, `save_calendar_event`, `send_email`, `read_engagement_inbox`, `write_memory`
