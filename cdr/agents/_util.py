from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from cdr.runtime import emit, emit_custom, emit_tool_call

try:
    from observability.otel import agent_span as _agent_span
except Exception:  # pragma: no cover - otel optional for local fixture runs
    @contextmanager
    def _agent_span(agent: str, pattern: str, run_id: str = ""):
        yield None


def with_span(agent: str, pattern: str, state: dict[str, Any]):
    return _agent_span(agent, pattern, str(state.get("run_id", "")))


def ping(
    state: dict[str, Any],
    agent: str,
    pattern: str,
    summary: str,
    status: str = "ok",
    artifact_ref: str | None = None,
) -> None:
    rid = str(state.get("run_id", ""))
    emit(rid, agent, pattern, summary, status=status, artifact_ref=artifact_ref)


def artifact(state: dict[str, Any], name: str, args: dict[str, Any]) -> None:
    """Mount one generative-UI card in the artifact drawer.

    `name` is a `render_*` tool from demo/fixtures/README.md. An unregistered
    name still renders as a raw-args card, so a new one is safe to add.
    """
    emit_tool_call(str(state.get("run_id", "")), name, args)


def panel(state: dict[str, Any], name: str, value: dict[str, Any]) -> None:
    """Push a board panel update (opportunities, pipeline, memory, engagement)."""
    emit_custom(str(state.get("run_id", "")), name, value)
