"""Canned agent output for demos and tests (USE_FIXTURES=1).

WHY THIS IS DATA AND NOT CODE
-----------------------------
This module used to be 286 lines of hardcoded Maya prose, so "fixture mode"
could only ever tell one story. The content now lives per persona:

    demo/<persona>/agents.json          canned output, keyed by agent class name
    demo/<persona>/profile.json         the creator
    demo/<persona>/opportunities_seed.json
    demo/<persona>/search_web.json      what search_web returns
    demo/<persona>/places.json          local brands for gap/collab scouting
    demo/<persona>/rag_corpus.json      past posts and notes
    demo/<persona>/inbox.json           week-2 replies
    demo/<persona>/analytics_week1.json week-1 performance

Adding a demo persona is adding a directory. Nothing here changes.

WHICH PERSONA IS ACTIVE
-----------------------
A ContextVar, set from the creator profile at the start of a run (see
`persona_for_profile`). It rides between services on the same header as the
tenant id, because a fixture demo spans all six of them.

Real accounts have no persona and fall back to `DEFAULT_PERSONA` -- fixtures
are a demo and test mode, and USE_FIXTURES defaults off.

THE RULES BLOCK
---------------
Most agents return a fixed payload. A few branch on what they were asked, which
is what makes the demo's critique-fails-then-passes beat work. Those entries
carry `_rules` instead of a literal payload:

    "FactCheckerAgent": {
      "_rules": [
        {"if_any": ["320 calories"], "then": {"verdict": "fail", ...}}
      ],
      "_default": {"verdict": "pass", "issues": [], "must_fix": []}
    }

Conditions match against the lowercased prompt: `if_any`, `if_all`, `if_none`.
`{oid}` anywhere in a payload is replaced with the opportunity id in play.
`{"_seed": "AgentName"}` pulls that agent's rows from opportunities_seed.json;
`{"_seed": "*"}` pulls all of them.
"""

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from typing import Any

DEMO = Path(__file__).resolve().parents[1] / "demo"
DEFAULT_PERSONA = "maya"

_persona: contextvars.ContextVar[str] = contextvars.ContextVar(
    "creatorloop_persona", default=""
)


# ---------------------------------------------------------------------------
# which persona
# ---------------------------------------------------------------------------


def personas() -> list[str]:
    """Every demo persona on disk, i.e. every directory with a profile.json."""
    if not DEMO.exists():
        return []
    return sorted(
        p.name for p in DEMO.iterdir() if p.is_dir() and (p / "profile.json").exists()
    )


def set_persona(name: str) -> contextvars.Token:
    return _persona.set((name or "").strip().lower())


def reset_persona(token: contextvars.Token) -> None:
    _persona.reset(token)


def current_persona() -> str:
    return _persona.get() or DEFAULT_PERSONA


def persona_for_profile(profile: dict[str, Any] | None) -> str:
    """Map a creator profile onto a demo persona, by handle then by niche.

    A real signup matches nothing and gets DEFAULT_PERSONA. That only matters
    with USE_FIXTURES=1, which is a demo and test mode.
    """
    if not profile:
        return DEFAULT_PERSONA

    handle = str(profile.get("handle") or "").lstrip("@").lower()
    niche = str(profile.get("niche") or "").lower()

    for name in personas():
        data = _read(DEMO / name / "profile.json", {})
        if handle and handle == str(data.get("handle", "")).lstrip("@").lower():
            return name

    # Fall back to niche keywords, so a hand-made account that looks like one of
    # the demo creators still demos coherently.
    for name in personas():
        data = _read(DEMO / name / "profile.json", {})
        keywords = [k.lower() for k in data.get("demo_niche_keywords", [])]
        if niche and any(k in niche for k in keywords):
            return name

    return DEFAULT_PERSONA


def use_persona_of(profile: dict[str, Any] | None) -> contextvars.Token:
    return set_persona(persona_for_profile(profile))


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def persona_dir(persona: str | None = None) -> Path:
    return DEMO / (persona or current_persona())


def persona_file(name: str, persona: str | None = None) -> Path:
    """Path to one of a persona's data files, falling back to Maya's.

    The fallback keeps a half-authored persona usable: a missing inbox.json
    shows Maya's rather than an empty panel mid-demo.
    """
    path = persona_dir(persona) / name
    if path.exists():
        return path
    return DEMO / DEFAULT_PERSONA / name


def load_persona_file(name: str, default: Any = None, persona: str | None = None) -> Any:
    return _read(persona_file(name, persona), default if default is not None else {})


def seed_opportunities(*source_agents: str) -> list[dict[str, Any]]:
    """This persona's seeded opportunities, optionally filtered by the agent
    that would have found them."""
    rows = load_persona_file("opportunities_seed.json", {}).get("opportunities") or []
    if not source_agents or "*" in source_agents:
        return list(rows)
    wanted = set(source_agents)
    return [r for r in rows if r.get("source_agent") in wanted]


# ---------------------------------------------------------------------------
# the little rules engine
# ---------------------------------------------------------------------------


def _matches(rule: dict[str, Any], blob: str) -> bool:
    if any(term.lower() not in blob for term in rule.get("if_all", [])):
        return False
    if any(term.lower() in blob for term in rule.get("if_none", [])):
        return False
    any_of = rule.get("if_any")
    if any_of and not any(term.lower() in blob for term in any_of):
        return False
    return True


def _resolve(value: Any, oid: str) -> Any:
    """Expand {oid} placeholders and {"_seed": ...} markers."""
    if isinstance(value, dict):
        if "_seed" in value:
            return {"opportunities": seed_opportunities(value["_seed"])}
        return {k: _resolve(v, oid) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, oid) for v in value]
    if isinstance(value, str):
        return value.replace("{oid}", oid)
    return value


def _default_oid(user: str) -> str:
    """The opportunity the prompt is about, if it names one we know."""
    for row in seed_opportunities():
        oid = str(row.get("id", ""))
        if oid and oid in user:
            return oid
    rows = seed_opportunities()
    return str(rows[0]["id"]) if rows else ""


def fixture_json(agent: str, user: str) -> dict[str, Any]:
    """Canned output for one named agent, for the persona in context."""
    entry = load_persona_file("agents.json", {}).get(agent)
    if entry is None:
        return {"ok": True, "agent": agent}

    oid = _default_oid(user)
    blob = (user or "").lower()

    if isinstance(entry, dict) and "_rules" in entry:
        for rule in entry["_rules"]:
            if _matches(rule, blob):
                return _resolve(rule.get("then", {}), oid)
        return _resolve(entry.get("_default", {"ok": True}), oid)

    return _resolve(entry, oid)


__all__ = [
    "DEFAULT_PERSONA",
    "current_persona",
    "fixture_json",
    "load_persona_file",
    "persona_dir",
    "persona_file",
    "persona_for_profile",
    "personas",
    "reset_persona",
    "seed_opportunities",
    "set_persona",
    "use_persona_of",
]
