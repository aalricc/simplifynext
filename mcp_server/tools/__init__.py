"""Tool registry for the MCP server.

Tools are plain async functions registered by name. `mcp_server/server.py` is a thin
dispatcher over this registry, so:

  * P1 edits `search.py`, P3 edits `pipeline.py` -- no shared file, no conflicts
  * a real FastMCP server can later expose the SAME functions with no rewrite
  * every tool is unit-testable without HTTP

Register with the decorator:

    @tool("search_web", owner="P1", description="Search the web")
    async def search_web(query: str, limit: int = 5) -> dict: ...
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    fn: ToolFn
    owner: str = ""
    description: str = ""
    kind: str = "tool"
    schema: dict[str, Any] = field(default_factory=dict)


REGISTRY: dict[str, ToolSpec] = {}


class ToolError(RuntimeError):
    """Raised when a tool is missing or its arguments are invalid."""


def tool(
    name: str,
    *,
    owner: str = "",
    description: str = "",
    kind: str = "tool",
) -> Callable[[ToolFn], ToolFn]:
    def decorator(fn: ToolFn) -> ToolFn:
        REGISTRY[name] = ToolSpec(
            name=name,
            fn=fn,
            owner=owner,
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
            kind=kind,
            schema=_schema_from_signature(fn),
        )
        return fn

    return decorator


def _schema_from_signature(fn: ToolFn) -> dict[str, Any]:
    """Best-effort JSON schema so /mcp/tools is self-describing for P2."""
    props: dict[str, Any] = {}
    required: list[str] = []
    types = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for pname, p in inspect.signature(fn).parameters.items():
        if pname == "kwargs":
            continue
        props[pname] = {"type": types.get(p.annotation, "string")}
        if p.default is inspect.Parameter.empty:
            required.append(pname)

    return {"type": "object", "properties": props, "required": required}


def list_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "owner": s.owner,
            "kind": s.kind,
            "description": s.description,
            "input_schema": s.schema,
        }
        for s in REGISTRY.values()
    ]


async def dispatch(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Invoke a registered tool by name. Raises ToolError on bad name/args."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name!r}; known: {sorted(REGISTRY)}")

    args = arguments or {}
    params = inspect.signature(spec.fn).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        unexpected = set(args) - set(params)
        if unexpected:
            raise ToolError(f"{name}: unexpected arguments {sorted(unexpected)}")

    try:
        return await spec.fn(**args)
    except TypeError as exc:
        raise ToolError(f"{name}: bad arguments -- {exc}") from exc


# Import tool modules so their decorators run. Keep at the bottom.
from mcp_server.tools import pipeline, search  # noqa: E402,F401
