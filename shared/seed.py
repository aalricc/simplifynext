"""Starter data for a new creator profile.

Two callers:

  * onboarding -- a brand-new profile needs an empty memory row and a small
    RAG corpus built from what the person told us, or the first campaign has
    nothing to retrieve and the memory panel renders blank.
  * scripts/seed_demo_user.py -- loads `demo/maya/*` through this same path, so
    the demo account is an ordinary user rather than a special case.

Everything here runs inside the tenant context, so callers must
`set_profile(...)` first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_manager import db
from shared.fixtures import DEMO

# Kept for callers that still import it; per-persona paths come from
# shared.fixtures.persona_file(), which falls back to Maya.
MAYA = Path(__file__).resolve().parents[1] / "demo" / "maya"


async def seed_new_profile(profile: dict[str, Any]) -> dict[str, int]:
    """What a fresh signup gets. Idempotent enough to re-run safely."""
    await db.write_memory({"wins": [], "losses": [], "next_bias": []})

    docs = list(_starter_documents(profile))
    for doc in docs:
        await db.add_rag_document(doc)
    return {"rag_documents": len(docs), "memory": 1}


def _starter_documents(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the onboarding answers into retrievable notes.

    The research agents call `retrieve_creator_memory` before they have any
    history to retrieve. Seeding the person's own answers means the first run
    is grounded in their niche and voice instead of returning nothing.
    """
    name = profile.get("name") or profile.get("display_name") or "this creator"
    niche = profile.get("niche", "")
    city = profile.get("city", "")
    voice = profile.get("voice") or {}
    docs: list[dict[str, Any]] = [
        {
            "title": f"{name} — positioning",
            "type": "profile",
            "text": (
                f"{name} makes {niche} content in {city}. "
                f"Goals: {'; '.join(profile.get('goals') or []) or 'not stated'}. "
                f"Pain: {profile.get('pain') or 'not stated'}."
            ),
            "tags": ["profile", "positioning"],
        },
        {
            "title": f"{name} — voice and constraints",
            "type": "voice",
            "text": (
                f"Tone: {voice.get('tone', 'not stated')}. "
                f"Language: {voice.get('language', 'not stated')}. "
                f"Signature: {voice.get('signature', 'not stated')}. "
                f"Avoid: {', '.join(voice.get('avoid') or []) or 'nothing stated'}. "
                f"Never cover: {', '.join(profile.get('no_go_topics') or []) or 'nothing stated'}."
            ),
            "tags": ["voice", "constraints"],
        },
    ]

    best = profile.get("best_performing") or []
    worst = profile.get("worst_performing") or []
    if best or worst:
        docs.append(
            {
                "title": f"{name} — what has worked so far",
                "type": "history",
                "text": (
                    f"Reported best-performing topics: {', '.join(best) or 'none given'}. "
                    f"Reported worst-performing: {', '.join(worst) or 'none given'}. "
                    "Self-reported at signup, not measured -- the adapt loop "
                    "overwrites this once real analytics arrive."
                ),
                "tags": ["history", "self-reported"],
            }
        )

    for target in profile.get("brand_targets") or []:
        if not isinstance(target, dict):
            continue
        docs.append(
            {
                "title": f"Brand target — {target.get('name', 'unnamed')}",
                "type": "brand_target",
                "text": (
                    f"{target.get('name', '')} in {target.get('area', 'unknown area')}. "
                    f"Why: {target.get('why', 'not stated')}."
                ),
                "tags": ["brand_target"],
            }
        )
    return docs


# ---------------------------------------------------------------------------
# demo data -- the Maya files, loaded as an ordinary profile
# ---------------------------------------------------------------------------


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def persona_profile_form(persona: str = "maya") -> dict[str, Any]:
    """A persona's `profile.json` in the shape `auth.create_profile` expects."""
    raw = _read(DEMO / persona / "profile.json", {})
    constraints = raw.get("constraints") or {}
    return {
        "handle": (raw.get("handle") or "@mayacooks.sg").lstrip("@"),
        "display_name": raw.get("name", "Maya Tan"),
        "city": raw.get("city", "Singapore"),
        "niche": raw.get("niche", ""),
        "pain": raw.get("pain", ""),
        "platforms": raw.get("platforms") or [],
        "followers": raw.get("followers") or {},
        "median_views": raw.get("median_views"),
        "cadence_goal": 3,
        "filming_days": constraints.get("filming_days") or [],
        "budget_sgd_per_week": constraints.get("budget_sgd_per_week"),
        "voice": raw.get("voice") or {},
        "goals": raw.get("goals") or [],
        "no_go": constraints.get("no_go") or [],
        "brand_targets": raw.get("brand_targets") or [],
        "best_performing": raw.get("best_performing") or [],
        "worst_performing": raw.get("worst_performing") or [],
    }


async def load_persona_corpus(persona: str = "maya") -> int:
    """A persona's `rag_corpus.json` -> rag_documents for the current profile."""
    data = _read(DEMO / persona / "rag_corpus.json", {})
    docs = data.get("documents", []) if isinstance(data, dict) else data
    count = 0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        await db.add_rag_document(
            {
                "title": doc.get("title", ""),
                # The corpus keeps prose in `notes`, not `text`.
                "text": doc.get("notes") or doc.get("text") or "",
                "type": doc.get("type", ""),
                "platform": doc.get("platform", ""),
                "tags": [t for t in (doc.get("type"), doc.get("platform")) if t],
            }
        )
        count += 1
    return count


async def load_persona_inbox(persona: str = "maya") -> int:
    data = _read(DEMO / persona / "inbox.json", {"items": []})
    count = 0
    for item in data.get("items", []):
        payload = {k: v for k, v in item.items() if k not in ("source", "opportunity_id")}
        await db.add_engagement_item(
            source=item.get("source", "email"),
            payload=payload,
            opportunity_id=item.get("opportunity_id"),
        )
        count += 1
    return count


async def load_persona_analytics(
    persona: str = "maya", median_views: int | None = None
) -> int:
    data = _read(DEMO / persona / "analytics_week1.json", {"posts": []})
    week = int(data.get("week") or 1)
    count = 0
    for post in data.get("posts", []):
        views = int(post.get("views") or 0)
        await db.add_analytics_post(
            {
                **post,
                "week": week,
                "platform": post.get("platform", "tiktok"),
                "median_ratio": (
                    round(views / median_views, 2) if median_views else None
                ),
            }
        )
        count += 1
    return count


# Back-compat aliases. The demo used to be Maya-only.
maya_profile_form = persona_profile_form
load_maya_corpus = load_persona_corpus
load_maya_inbox = load_persona_inbox
load_maya_analytics = load_persona_analytics
