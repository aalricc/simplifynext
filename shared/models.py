"""Every table in CreatorLoop, on one declarative base.

The repo rule for `shared/schemas.py` -- "if a field is not in it, it does not
exist" -- extends here: **if a table is not in this module, it does not exist**.
Schema changes go through a PR that P1 merges, plus an Alembic revision.

TENANCY
-------
`creator_profiles` is the tenancy unit, not `users`. One user may own several
profiles (a creator running two accounts), so every row that belongs to a
creator hangs off `profile_id`. Switching profiles then cannot leak data
sideways, and a query that forgets the tenant is a missing-column error rather
than a silent cross-tenant read.

`opportunities` and `calendar_events` use a COMPOSITE primary key
`(profile_id, id)` on purpose. Their ids are human-authored strings that show
up on screen and in the AG-UI stream (`opp-laksa-weeknight`), so they are only
unique *within* one creator.

PORTABILITY
-----------
Postgres is the target (JSONB, real concurrency). The column types below are
SQLAlchemy generics with a Postgres variant, so the same models also run on
SQLite for tests and for a keyless local dev loop. Do not reach for a
Postgres-only construct without adding a variant here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, plain JSON on SQLite. One alias so no model has to care.
Json = JSON().with_variant(JSONB, "postgresql")

# BIGSERIAL on Postgres. SQLite only auto-increments a column declared exactly
# `INTEGER PRIMARY KEY` -- a BIGINT one silently fails NOT NULL on insert -- so
# the variant is required, not a nicety.
BigIntPk = BigInteger().with_variant(Integer, "sqlite")

# Opportunity ids are authored strings; calendar ids are derived from them.
OID = String(160)
CAL_ID = String(240)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    from sqlalchemy import Uuid

    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Stored lowercased by the auth layer so the unique index is the real
    # case-insensitive guard without needing the citext extension.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    """Server-side sessions, not stateless JWTs.

    We need "log out everywhere" and instant revocation, and one indexed lookup
    per request costs nothing next to a multi-minute campaign run. Only the
    SHA-256 of the cookie value is stored -- a database leak must not hand
    anyone a working session.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class LoginAttempt(Base):
    """Failed logins, so the login route can rate-limit per email.

    Without this the login endpoint is a password oracle: unlimited guesses at
    whatever rate the attacker can manage.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


# ---------------------------------------------------------------------------
# the creator -- tenancy unit
# ---------------------------------------------------------------------------


class CreatorProfile(Base):
    """One creator account. This is what `demo/maya/profile.json` becomes.

    Fields mirror that file so the existing agent prompts keep working and
    onboarding has an obvious shape.
    """

    __tablename__ = "creator_profiles"
    __table_args__ = (UniqueConstraint("user_id", "handle", name="uq_profile_user_handle"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    handle: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    niche: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    pain: Mapped[str] = mapped_column(Text, nullable=False, default="")

    platforms: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    followers: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    median_views: Mapped[int | None] = mapped_column(Integer)

    cadence_goal: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    filming_days: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    budget_sgd_per_week: Mapped[int | None] = mapped_column(Integer)

    # {tone, language, signature, avoid[]}
    voice: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    goals: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    no_go: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    brand_targets: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    best_performing: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    worst_performing: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def to_profile_dict(self) -> dict[str, Any]:
        """The shape agents and the board expect (`demo/maya/profile.json`)."""
        return {
            "id": str(self.id),
            "profile_id": str(self.id),
            "name": self.display_name,
            "handle": self.handle,
            "city": self.city,
            "niche": self.niche,
            "pain": self.pain,
            "platforms": list(self.platforms or []),
            "followers": dict(self.followers or {}),
            "median_views": self.median_views,
            "posting_cadence_goal": f"{self.cadence_goal} per week",
            "posting_cadence_per_week": self.cadence_goal,
            "goals": list(self.goals or []),
            "voice": dict(self.voice or {}),
            "brand_voice": (self.voice or {}).get("tone", ""),
            "no_go_topics": list(self.no_go or []),
            "audience": f"{self.niche} audience in {self.city}",
            "constraints": {
                "filming_days": list(self.filming_days or []),
                "budget_sgd_per_week": self.budget_sgd_per_week,
                "no_go": list(self.no_go or []),
            },
            "brand_targets": list(self.brand_targets or []),
            "best_performing": list(self.best_performing or []),
            "worst_performing": list(self.worst_performing or []),
        }


def profile_fk(*, primary_key: bool = False) -> Mapped[uuid.UUID]:
    from sqlalchemy import Uuid

    return mapped_column(
        Uuid,
        ForeignKey("creator_profiles.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
        # A composite PK already indexes (profile_id, id) left-to-right.
        index=not primary_key,
    )


# ---------------------------------------------------------------------------
# pipeline -- was pipeline_manager/pipeline.db, now tenant-scoped
# ---------------------------------------------------------------------------


class Opportunity(Base):
    __tablename__ = "opportunities"

    profile_id: Mapped[uuid.UUID] = profile_fk(primary_key=True)
    # Not globally unique -- `opp-laksa-weeknight` may exist for many creators.
    id: Mapped[str] = mapped_column(OID, primary_key=True)

    record: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="new")
    qualification: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_profile_opp", "profile_id", "opportunity_id"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    profile_id: Mapped[uuid.UUID] = profile_fk()
    opportunity_id: Mapped[str | None] = mapped_column(OID)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    record: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    profile_id: Mapped[uuid.UUID] = profile_fk(primary_key=True)
    id: Mapped[str] = mapped_column(CAL_ID, primary_key=True)
    opportunity_id: Mapped[str | None] = mapped_column(OID)
    slot: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="post")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Memory(Base):
    """What the agent learned, per creator.

    The SQLite version of this table was `CHECK (id = 1)` -- one row for the
    whole world, which was the single-tenant assumption written in SQL. The
    primary key is now the profile.
    """

    __tablename__ = "memory"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    wins: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    losses: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    next_bias: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# was demo/maya/*.json
# ---------------------------------------------------------------------------


class RagDocument(Base):
    """Was `demo/maya/rag_corpus.json`. Keyword-scored by shared/rag.py."""

    __tablename__ = "rag_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = profile_fk()
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doc_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    tags: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class EngagementItem(Base):
    """Was `demo/maya/inbox.json`. Inbound replies and comments."""

    __tablename__ = "engagement_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = profile_fk()
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="email")
    payload: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    classification: Mapped[dict[str, Any] | None] = mapped_column(Json)
    opportunity_id: Mapped[str | None] = mapped_column(OID)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unread")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AnalyticsPost(Base):
    """Was `demo/maya/analytics_week1.json`. Feeds the adapt loop.

    Field names match what `PerformanceAdaptAgent` actually reads -- title,
    views, saves, avg_watch_pct -- so the agent needs no translation layer.
    `topic` and `median_ratio` are ours: the agent's next-week bias is more
    useful grouped by topic than by post title.
    """

    __tablename__ = "analytics_posts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = profile_fk()
    external_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    week: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_watch_pct: Mapped[float | None] = mapped_column(Float)
    # views / this profile's median_views, computed on write.
    median_ratio: Mapped[float | None] = mapped_column(Float)
    # Generated by shared/simulate.py rather than measured. Never let a caller
    # present these as real performance data.
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


# ---------------------------------------------------------------------------
# runs -- was cdr/runtime.py's in-memory RUNS dict
# ---------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[uuid.UUID] = profile_fk()
    week: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # packages / outreach / qa, written once at the end of the run.
    result: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)


class RunEvent(Base):
    """One trace row: agent, pattern, status, summary. Mirrors shared.schemas.RunEvent."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    agent: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    pattern: Mapped[str] = mapped_column(String(20), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifact_ref: Mapped[str | None] = mapped_column(Text)


class AguiEvent(Base):
    """The raw AG-UI frame, in order.

    Same shape as one line of `demo/fixtures/*.jsonl`, which is what makes this
    table replace the fixture replay: reconnect mid-run, run history, and
    replay all fall out of it.
    """

    __tablename__ = "agui_events"
    __table_args__ = (Index("ix_agui_events_run_seq", "run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


__all__ = [
    "Base",
    "User",
    "Session",
    "LoginAttempt",
    "CreatorProfile",
    "Opportunity",
    "Artifact",
    "CalendarEvent",
    "Memory",
    "RagDocument",
    "EngagementItem",
    "AnalyticsPost",
    "Run",
    "RunEvent",
    "AguiEvent",
    "utcnow",
]
