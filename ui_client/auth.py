"""Accounts, sessions, and the tenant hand-off.

ui_client is the only service that authenticates a human. Everything behind it
trusts the signed header this module mints (shared/tenant.py), which is why
the backend services do not publish ports.

WHAT IS STORED
--------------
* passwords  -- argon2id hash only, never the password
* sessions   -- SHA-256 of the cookie value, never the cookie value itself

A database dump therefore hands an attacker neither a usable password nor a
usable session.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, func, select

from shared.db import session
from shared.models import CreatorProfile, LoginAttempt, Session, User, utcnow

COOKIE_NAME = "creatorloop_session"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))

# Rate limiting on the login route. Without it, the endpoint is a password
# oracle: unlimited guesses at whatever rate the network allows.
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(minutes=15)

MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()


class AuthError(Exception):
    """Anything the caller should see as a 4xx: bad credentials, taken email."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def normalize_email(email: str) -> str:
    """Lowercased and trimmed, so the unique index is the case-insensitive guard."""
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AuthError("That does not look like an email address.")
    return email


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def create_session(user_id: uuid.UUID, user_agent: str = "") -> str:
    """Return the raw cookie value. Only its hash reaches the database."""
    raw = secrets.token_urlsafe(32)
    async with session() as s:
        s.add(
            Session(
                user_id=user_id,
                token_hash=_token_hash(raw),
                expires_at=utcnow() + timedelta(days=SESSION_DAYS),
                user_agent=(user_agent or "")[:300],
            )
        )
    return raw


async def resolve_session(raw: str | None) -> User | None:
    """The signed-in user, or None. Expired and revoked sessions return None."""
    if not raw:
        return None
    async with session() as s:
        row = await s.scalar(
            select(Session).where(Session.token_hash == _token_hash(raw))
        )
        if row is None or row.revoked_at is not None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < utcnow():
            return None
        return await s.get(User, row.user_id)


async def revoke_session(raw: str | None) -> None:
    if not raw:
        return
    async with session() as s:
        row = await s.scalar(
            select(Session).where(Session.token_hash == _token_hash(raw))
        )
        if row is not None:
            row.revoked_at = utcnow()


async def revoke_all_sessions(user_id: uuid.UUID) -> None:
    """Log out everywhere. The reason this is a table and not a stateless JWT."""
    async with session() as s:
        rows = (
            await s.scalars(
                select(Session).where(
                    Session.user_id == user_id, Session.revoked_at.is_(None)
                )
            )
        ).all()
        for row in rows:
            row.revoked_at = utcnow()


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


async def _record_attempt(email: str) -> None:
    async with session() as s:
        s.add(LoginAttempt(email=email))


async def _too_many_attempts(email: str) -> bool:
    cutoff = utcnow() - ATTEMPT_WINDOW
    async with session() as s:
        # Opportunistic cleanup, so the table cannot grow without bound.
        await s.execute(
            delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff)
        )
        count = await s.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.email == email, LoginAttempt.attempted_at >= cutoff)
        )
    return int(count or 0) >= MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# signup / login
# ---------------------------------------------------------------------------


async def signup(email: str, password: str, display_name: str = "") -> User:
    email = normalize_email(email)
    validate_password(password)
    async with session() as s:
        existing = await s.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise AuthError("An account with that email already exists.", status=409)
        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name=(display_name or email.split("@")[0])[:120],
        )
        s.add(user)
        await s.flush()
        await s.refresh(user)
        return user


async def login(email: str, password: str) -> User:
    email = normalize_email(email)
    if await _too_many_attempts(email):
        raise AuthError(
            "Too many sign-in attempts. Wait 15 minutes and try again.", status=429
        )

    async with session() as s:
        user = await s.scalar(select(User).where(User.email == email))

    # Same message and roughly the same work either way, so the response does
    # not reveal whether the email exists.
    if user is None or not verify_password(user.password_hash, password):
        if user is None:
            hash_password(password)  # equalise timing
        await _record_attempt(email)
        raise AuthError("Email or password is incorrect.", status=401)

    async with session() as s:
        fresh = await s.get(User, user.id)
        if fresh is not None:
            fresh.last_login_at = utcnow()
        await s.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    return user


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


async def list_profiles(user_id: uuid.UUID) -> list[CreatorProfile]:
    async with session() as s:
        return list(
            (
                await s.scalars(
                    select(CreatorProfile)
                    .where(CreatorProfile.user_id == user_id)
                    .order_by(CreatorProfile.created_at)
                )
            ).all()
        )


async def get_profile(user_id: uuid.UUID, profile_id: str) -> CreatorProfile | None:
    """Scoped by user_id on purpose: a profile id from the client is untrusted."""
    try:
        pid = uuid.UUID(str(profile_id))
    except (ValueError, TypeError):
        return None
    async with session() as s:
        row = await s.get(CreatorProfile, pid)
        return row if row is not None and row.user_id == user_id else None


async def create_profile(user_id: uuid.UUID, form: dict[str, Any]) -> CreatorProfile:
    handle = str(form.get("handle") or "").strip().lstrip("@")
    if not handle:
        raise AuthError("Pick a handle for your creator account.")

    async with session() as s:
        clash = await s.scalar(
            select(CreatorProfile).where(
                CreatorProfile.user_id == user_id, CreatorProfile.handle == handle
            )
        )
        if clash is not None:
            raise AuthError(f"You already have a profile called @{handle}.", status=409)

        row = CreatorProfile(
            user_id=user_id,
            handle=handle,
            display_name=str(form.get("display_name") or handle)[:120],
            city=str(form.get("city") or "")[:120],
            niche=str(form.get("niche") or "")[:200],
            pain=str(form.get("pain") or ""),
            platforms=list(form.get("platforms") or []),
            followers=dict(form.get("followers") or {}),
            median_views=_opt_int(form.get("median_views")),
            cadence_goal=int(form.get("cadence_goal") or 3),
            filming_days=list(form.get("filming_days") or []),
            budget_sgd_per_week=_opt_int(form.get("budget_sgd_per_week")),
            voice=dict(form.get("voice") or {}),
            goals=list(form.get("goals") or []),
            no_go=list(form.get("no_go") or []),
            brand_targets=list(form.get("brand_targets") or []),
            best_performing=list(form.get("best_performing") or []),
            worst_performing=list(form.get("worst_performing") or []),
        )
        s.add(row)
        await s.flush()
        await s.refresh(row)
        return row


async def update_profile(
    user_id: uuid.UUID, profile_id: str, form: dict[str, Any]
) -> CreatorProfile:
    row = await get_profile(user_id, profile_id)
    if row is None:
        raise AuthError("Profile not found.", status=404)

    scalars = {
        "display_name": str,
        "city": str,
        "niche": str,
        "pain": str,
    }
    lists = (
        "platforms",
        "filming_days",
        "goals",
        "no_go",
        "brand_targets",
        "best_performing",
        "worst_performing",
    )
    async with session() as s:
        fresh = await s.get(CreatorProfile, row.id)
        assert fresh is not None
        for field, cast in scalars.items():
            if field in form:
                setattr(fresh, field, cast(form[field]))
        for field in lists:
            if field in form:
                setattr(fresh, field, list(form[field] or []))
        for field in ("followers", "voice"):
            if field in form:
                setattr(fresh, field, dict(form[field] or {}))
        if "cadence_goal" in form:
            fresh.cadence_goal = int(form["cadence_goal"] or 3)
        if "median_views" in form:
            fresh.median_views = _opt_int(form["median_views"])
        if "budget_sgd_per_week" in form:
            fresh.budget_sgd_per_week = _opt_int(form["budget_sgd_per_week"])
        await s.flush()
        await s.refresh(fresh)
        return fresh


def _opt_int(value: Any) -> int | None:
    if value in (None, "", []):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
