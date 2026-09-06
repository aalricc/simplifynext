"""Graph state and LLM draft models for the Opportunity Finder.

`Opportunity` in shared/schemas.py stays the authority for what leaves this
service. The LLM only fills `OpportunityDraft` -- the descriptive fields it is
actually good at. We attach id / city / niche / source_agent / status ourselves
so those can never drift.
"""

from __future__ import annotations

import json
import operator
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from shared.fixtures import use_persona_of
from shared.schemas import CreatorProfile, Opportunity, OpportunityStatus, OpportunityType

DEMO = Path(__file__).resolve().parents[2] / "demo" / "maya"
SEED = DEMO / "opportunities_seed.json"
PROFILE = DEMO / "profile.json"


# --------------------------------------------------------------------------
# LLM output models
# --------------------------------------------------------------------------


class OpportunityDraft(BaseModel):
    """What an LLM agent returns. No id/source_agent -- we own those."""

    type: OpportunityType
    title: str
    why_now: str
    score: int = Field(ge=0, le=100, description="0-100 fit for this creator")
    evidence_urls: list[str] = Field(default_factory=list)
    raw_notes: str = ""


class DraftList(BaseModel):
    opportunities: list[OpportunityDraft] = Field(default_factory=list)


class QueryList(BaseModel):
    queries: list[str] = Field(default_factory=list, description="5-10 web search queries")


class ScoredItem(BaseModel):
    id: str
    score: int = Field(ge=0, le=100)
    reason: str = ""


class ScoreList(BaseModel):
    scored: list[ScoredItem] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Graph state
# --------------------------------------------------------------------------


class FinderState(TypedDict, total=False):
    run_id: str
    profile: dict[str, Any]
    niche: str
    city: str
    limit: int
    memory: dict[str, Any]

    queries: list[str]
    # The three harvest agents run concurrently and all append here, so this
    # needs a reducer -- without operator.add LangGraph raises on the parallel
    # writes instead of merging them.
    harvested: Annotated[list[dict[str, Any]], operator.add]
    clustered: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]
    notes: Annotated[list[str], operator.add]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or uuid.uuid4().hex[:8]


def draft_to_opportunity(
    draft: OpportunityDraft, *, city: str, niche: str, source_agent: str
) -> Opportunity:
    return Opportunity(
        id=f"opp-{slugify(draft.title)}",
        type=draft.type,
        title=draft.title,
        why_now=draft.why_now,
        city=city,
        niche=niche,
        score=draft.score,
        evidence_urls=draft.evidence_urls,
        raw_notes=draft.raw_notes,
        source_agent=source_agent,
        status=OpportunityStatus.new,
    )


class ProfileMissing(RuntimeError):
    """No creator on the request. Never substitute a demo persona."""


def load_profile(profile: dict[str, Any] | None = None) -> CreatorProfile:
    """The creator this search is for.

    There is deliberately no fallback. Returning a hardcoded persona here meant
    a request that lost its profile still produced confident, wrong results --
    Singapore hawker opportunities for a creator in Lisbon.
    """
    if not profile:
        raise ProfileMissing(
            "Opportunity search requires a creator profile. The request "
            "arrived without one."
        )
    # Callers send the board's profile shape, which is a superset of
    # CreatorProfile and keys the id as `profile_id` in some paths.
    data = dict(profile)
    data.setdefault("id", data.get("profile_id", ""))
    data.setdefault("name", data.get("display_name", ""))
    if not data.get("id"):
        raise ProfileMissing("Creator profile has no id.")
    # Fixture mode serves this creator's demo data, not Maya's. No-op live.
    use_persona_of(data)
    return CreatorProfile(**data)


def seed_opportunities(limit: int = 8, source_agent: str | None = None) -> list[dict[str, Any]]:
    """Maya's seed opportunities. TESTS AND THE DEMO SEED SCRIPT ONLY.

    This was the day-1 fallback for every harvest agent, so a failed LLM call
    still returned confident results. With real accounts that means handing one
    creator's Singapore hawker opportunities to another creator and calling
    them findings.

    Agents must not call this. `CREATORLOOP_ALLOW_SEED=1` unlocks it for
    scripts/seed_demo_user.py and the test suite.
    """
    if os.getenv("CREATORLOOP_ALLOW_SEED", "0") not in ("1", "true", "True"):
        return []
    if not SEED.exists():
        return []
    data = json.loads(SEED.read_text()).get("opportunities", [])
    if source_agent:
        data = [o for o in data if o.get("source_agent") == source_agent]
    return data[:limit]


def profile_brief(p: CreatorProfile) -> str:
    """Compact profile block reused by every agent prompt."""
    return (
        f"Creator: {p.name}\n"
        f"Niche: {p.niche}\n"
        f"City: {p.city}\n"
        f"Platforms: {', '.join(p.platforms) or 'tiktok, instagram'}\n"
        f"Audience: {p.audience}\n"
        f"Goals: {'; '.join(p.goals)}\n"
        f"Brand voice: {p.brand_voice}\n"
        f"Never cover: {', '.join(p.no_go_topics) or 'n/a'}\n"
        f"Posts per week: {p.posting_cadence_per_week}"
    )


def format_hits(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "(no search results)"
    return "\n".join(
        f"- {h.get('title','')} [{h.get('url','')}]\n  {h.get('snippet','')}" for h in hits
    )
