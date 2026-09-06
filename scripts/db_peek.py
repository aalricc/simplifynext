#!/usr/bin/env python3
"""Look inside the database without leaving the repo.

    python scripts/db_peek.py                  # row counts + accounts
    python scripts/db_peek.py opportunities    # dump one table
    python scripts/db_peek.py --profile maya   # everything for one creator

Works against whatever DATABASE_URL points at -- SQLite or Postgres -- so the
same command answers "is my data actually there" in either setup.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import func, select  # noqa: E402

from shared.db import _safe_url, session  # noqa: E402
from shared.models import (  # noqa: E402
    AguiEvent,
    AnalyticsPost,
    Artifact,
    Base,
    CalendarEvent,
    CreatorProfile,
    EngagementItem,
    Memory,
    Opportunity,
    RagDocument,
    Run,
    RunEvent,
    Session,
    User,
)

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"

# Ordered for reading, not alphabetically: accounts, then the creator, then
# their data, then run history.
MODELS = [
    User,
    Session,
    CreatorProfile,
    Opportunity,
    Artifact,
    CalendarEvent,
    Memory,
    RagDocument,
    EngagementItem,
    AnalyticsPost,
    Run,
    RunEvent,
    AguiEvent,
]
BY_NAME = {m.__tablename__: m for m in MODELS}


async def overview() -> None:
    print(f"\n{BOLD}{_safe_url()}{RESET}\n")
    async with session() as s:
        width = max(len(m.__tablename__) for m in MODELS)
        for model in MODELS:
            count = await s.scalar(select(func.count()).select_from(model))
            marker = "" if count else f"  {DIM}empty{RESET}"
            print(f"  {model.__tablename__:<{width}}  {count:>6}{marker}")

        users = (await s.scalars(select(User))).all()
        if not users:
            print(
                f"\n{DIM}No accounts yet. Create one at /signup, or run"
                f"\n  python scripts/seed_demo_user.py{RESET}"
            )
            return

        print(f"\n{BOLD}accounts{RESET}")
        for user in users:
            profiles = (
                await s.scalars(
                    select(CreatorProfile).where(CreatorProfile.user_id == user.id)
                )
            ).all()
            last = user.last_login_at.strftime("%Y-%m-%d %H:%M") if user.last_login_at else "never"
            print(f"  {user.email}   {DIM}last login {last}{RESET}")
            for p in profiles:
                opps = await s.scalar(
                    select(func.count())
                    .select_from(Opportunity)
                    .where(Opportunity.profile_id == p.id)
                )
                runs = await s.scalar(
                    select(func.count()).select_from(Run).where(Run.profile_id == p.id)
                )
                print(
                    f"    @{p.handle}  {DIM}{p.niche or 'no niche'} · {p.city or 'no city'}"
                    f" · {opps} opportunities · {runs} runs{RESET}"
                )
                print(f"    {DIM}{p.id}{RESET}")


async def dump(table: str, limit: int) -> None:
    model = BY_NAME.get(table)
    if model is None:
        print(f"Unknown table {table!r}.\nKnown: {', '.join(sorted(BY_NAME))}")
        raise SystemExit(2)

    async with session() as s:
        rows = (await s.scalars(select(model).limit(limit))).all()
    if not rows:
        print(f"{table} is empty.")
        return

    columns = [c.name for c in model.__table__.columns]
    print(f"\n{BOLD}{table}{RESET}  {DIM}({len(rows)} shown){RESET}\n")
    for row in rows:
        for col in columns:
            value = getattr(row, col)
            if col in ("password_hash", "token_hash"):
                value = f"{DIM}<redacted>{RESET}"
            text = str(value)
            if len(text) > 110:
                text = text[:107] + "…"
            print(f"  {col:<18} {text}")
        print()


async def profile_detail(needle: str) -> None:
    async with session() as s:
        profiles = (await s.scalars(select(CreatorProfile))).all()
        match = next(
            (p for p in profiles if needle.lower() in f"{p.handle} {p.display_name} {p.id}".lower()),
            None,
        )
        if match is None:
            print(f"No profile matching {needle!r}. Known: {[p.handle for p in profiles]}")
            raise SystemExit(2)

        print(f"\n{BOLD}@{match.handle}{RESET}  {match.display_name}")
        print(f"  {DIM}{match.id}{RESET}")
        print(f"  {match.niche or '-'} · {match.city or '-'}\n")

        for model, label in (
            (Opportunity, "opportunities"),
            (Artifact, "artifacts"),
            (CalendarEvent, "calendar events"),
            (RagDocument, "rag documents"),
            (EngagementItem, "inbox items"),
            (AnalyticsPost, "analytics posts"),
            (Run, "runs"),
        ):
            count = await s.scalar(
                select(func.count()).select_from(model).where(model.profile_id == match.id)
            )
            print(f"  {label:<18} {count}")

        memory = await s.get(Memory, match.id)
        if memory:
            print(f"\n{BOLD}memory{RESET}")
            for field in ("wins", "losses", "next_bias"):
                for item in getattr(memory, field) or []:
                    print(f"  {field:<10} {item}")

        opps = (
            await s.scalars(
                select(Opportunity).where(Opportunity.profile_id == match.id).limit(10)
            )
        ).all()
        if opps:
            print(f"\n{BOLD}opportunities{RESET}")
            for o in opps:
                title = (o.record or {}).get("title", "")
                print(f"  {o.status:<12} {o.qualification or '-':<6} {title[:60]}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", nargs="?", help="dump one table")
    parser.add_argument("--profile", help="handle, name or id of one creator")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if args.profile:
        asyncio.run(profile_detail(args.profile))
    elif args.table:
        asyncio.run(dump(args.table, args.limit))
    else:
        asyncio.run(overview())
