#!/usr/bin/env python3
"""Create the demo accounts. Each persona becomes an ordinary user.

    python scripts/seed_demo_user.py                # every persona in demo/
    python scripts/seed_demo_user.py henry          # just one
    python scripts/seed_demo_user.py --list         # what is available
    python scripts/seed_demo_user.py --force        # reload data into existing profiles

Each persona in `demo/<name>/` is loaded through the ordinary signup +
onboarding path, so a demo account is an account like any other. Nothing in the
services special-cases them.

Sign-in is `<persona>@creatorloop.local` with the shared demo password, so you
can switch personas on camera without hunting for credentials.

Safe to re-run: existing users and profiles are reused rather than duplicated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402

load_dotenv()

# The seed loader is gated so agents cannot reach it at runtime; this script is
# one of the two callers that legitimately can.
os.environ["CREATORLOOP_ALLOW_SEED"] = "1"

from shared.db import create_all, is_sqlite, session  # noqa: E402
from shared.fixtures import personas  # noqa: E402
from shared.models import User  # noqa: E402
from shared.seed import (  # noqa: E402
    load_persona_analytics,
    load_persona_corpus,
    load_persona_inbox,
    persona_profile_form,
)
from shared.tenant import reset_profile, set_profile  # noqa: E402
from ui_client import auth  # noqa: E402

DEMO_PASSWORD = "creatorloop-demo"


def email_for(persona: str) -> str:
    return f"{persona}@creatorloop.local"


async def seed_one(persona: str, password: str, force: bool) -> dict:
    email = email_for(persona)

    async with session() as s:
        user = await s.scalar(select(User).where(User.email == email))
    if user is None:
        user = await auth.signup(email, password, display_name=f"{persona.title()} (demo)")

    form = persona_profile_form(persona)
    existing = next(
        (p for p in await auth.list_profiles(user.id) if p.handle == form["handle"]), None
    )

    if existing is not None and not force:
        return {
            "persona": persona,
            "email": email,
            "handle": existing.handle,
            "id": str(existing.id),
            "status": "exists",
        }

    profile = existing or await auth.create_profile(user.id, form)

    ctx = set_profile(str(profile.id))
    try:
        from pipeline_manager import db

        await db.write_memory({"wins": [], "losses": [], "next_bias": []})
        docs = await load_persona_corpus(persona)
        mails = await load_persona_inbox(persona)
        posts = await load_persona_analytics(persona, median_views=profile.median_views)
    finally:
        reset_profile(ctx)

    return {
        "persona": persona,
        "email": email,
        "handle": profile.handle,
        "id": str(profile.id),
        "status": "seeded",
        "counts": f"{docs} docs, {mails} inbox, {posts} analytics",
    }


async def main(wanted: list[str], password: str, force: bool) -> int:
    if is_sqlite():
        await create_all()

    available = personas()
    unknown = [p for p in wanted if p not in available]
    if unknown:
        print(f"Unknown persona(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(available)}")
        return 2

    results = [await seed_one(p, password, force) for p in (wanted or available)]

    width = max(len(r["persona"]) for r in results)
    print()
    for r in results:
        note = r.get("counts", "already seeded — use --force to reload")
        print(f"  {r['persona']:<{width}}  @{r['handle']:<16} {r['status']:<7} {note}")

    print("\n  sign in at  http://localhost:8000/signin")
    print(f"  password    {password}   (all demo accounts)")
    for r in results:
        print(f"  {r['persona']:<{width}}  {r['email']}")
    print()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("personas", nargs="*", help="which to seed (default: all)")
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--force", action="store_true", help="reload demo data")
    parser.add_argument("--list", action="store_true", help="list personas and exit")
    args = parser.parse_args()

    if args.list:
        print("\n".join(personas()))
        raise SystemExit(0)

    raise SystemExit(asyncio.run(main(args.personas, args.password, args.force)))
