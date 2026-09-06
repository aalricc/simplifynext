from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OpportunityType(str, Enum):
    trend = "trend"
    gap = "gap"
    collab = "collab"
    brand = "brand"


class OpportunityStatus(str, Enum):
    new = "new"
    researched = "researched"
    packaged = "packaged"
    outreached = "outreached"
    engaged = "engaged"
    meeting = "meeting"
    won = "won"
    lost = "lost"


class PatternKind(str, Enum):
    parallel = "parallel"
    sequential = "sequential"
    loop = "loop"
    tool = "tool"
    custom = "custom"
    llm = "llm"


class CreatorProfile(BaseModel):
    id: str
    name: str
    niche: str
    city: str
    platforms: list[str] = Field(default_factory=list)
    audience: str = ""
    goals: list[str] = Field(default_factory=list)
    brand_voice: str = ""
    no_go_topics: list[str] = Field(default_factory=list)
    posting_cadence_per_week: int = 3


class SearchRequest(BaseModel):
    # Required. It defaulted to "maya", so a request that lost its creator
    # still searched -- confidently, for the wrong person.
    profile_id: str
    niche: str
    city: str
    limit: int = 8
    profile: CreatorProfile | None = None


class Opportunity(BaseModel):
    id: str
    type: OpportunityType
    title: str
    why_now: str
    city: str
    niche: str
    score: int = Field(ge=0, le=100)
    evidence_urls: list[str] = Field(default_factory=list)
    raw_notes: str = ""
    source_agent: str
    status: OpportunityStatus = OpportunityStatus.new


class ResearchBrief(BaseModel):
    opportunity_id: str
    audience_insight: str
    peer_moves: str
    platform_presence: str
    pain_points: str
    evidence_urls: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class WeekPlanItem(BaseModel):
    hook: str
    format: str
    platform: str
    posting_slot: str


class ContentPackage(BaseModel):
    opportunity_id: str
    week_plan: list[WeekPlanItem]
    hero_script: str
    captions: dict[str, str] = Field(default_factory=dict)
    cta: str
    sources: list[str] = Field(default_factory=list)


class QAVerdict(BaseModel):
    agent: str
    verdict: Literal["pass", "fail"]
    issues: list[str] = Field(default_factory=list)
    must_fix: list[str] = Field(default_factory=list)
    iteration: int = 1


class OutreachDraft(BaseModel):
    opportunity_id: str
    channel: Literal["email", "dm", "call_script"]
    to: str
    subject: str = ""
    body: str
    status: Literal["drafted", "sent_mock", "awaiting_send"] = "drafted"


class CalendarEvent(BaseModel):
    id: str
    opportunity_id: str
    slot: datetime
    kind: Literal["post", "followup", "meeting"]
    title: str


class EngagementEvent(BaseModel):
    source: Literal["email", "analytics", "comment"]
    payload: dict[str, Any]
    opportunity_id: str | None = None


class MemoryState(BaseModel):
    wins: list[str] = Field(default_factory=list)
    losses: list[str] = Field(default_factory=list)
    next_bias: list[str] = Field(default_factory=list)


class RunEvent(BaseModel):
    ts: datetime
    agent: str
    pattern: PatternKind
    status: Literal["started", "ok", "fail", "awaiting_send"]
    summary: str
    artifact_ref: str | None = None
    run_id: str


class RunState(BaseModel):
    run_id: str
    profile_id: str
    current_agent: str
    opportunity_ids: list[str] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)
    packages: list[ContentPackage] = Field(default_factory=list)
    outreach: list[OutreachDraft] = Field(default_factory=list)
    qa: list[QAVerdict] = Field(default_factory=list)
    memory_delta: MemoryState | None = None
