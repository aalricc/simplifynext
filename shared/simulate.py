"""Synthetic engagement, so the adapt loop is demonstrable in a demo.

`POST /engagement/replay_maya_week2` used to be the "week 2" button: it read
one hardcoded file for one persona. Any creator needs a week 2, and nobody
waits a real week to see whether the agent adapts -- so this generates a
plausible week from the profile's own opportunities and topics.

WHAT THIS IS AND IS NOT
-----------------------
It is a dev/demo affordance. It is not analytics, and the numbers are not
measurements. `ui_client` refuses to expose it when CREATORLOOP_ENV=production,
and every generated row is tagged `"synthetic": True` so nothing downstream can
quietly present it as real performance data.

Real analytics arrive through `POST /engagement/ingest` from whatever platform
integration you add later; this writes to the same tables.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from pipeline_manager import db
from shared.models import utcnow

# Deterministic per profile, so a demo repeats and a bug is reproducible.
_REPLY_TEMPLATES = [
    (
        "interested",
        "Re: {subject}",
        "Hi {name}, we liked the {topic} angle. Interested in a short trial "
        "next month. Can we hop on a 15-minute call?",
    ),
    (
        "interested",
        "Re: {subject}",
        "Thanks for reaching out — the {topic} idea is a good fit for us. "
        "What would a first collaboration look like?",
    ),
    (
        "not_interested",
        "Re: {subject}",
        "Thanks {name}, but we are not taking on creator work this quarter. "
        "Do keep us in mind later.",
    ),
]


def _seeded_random(profile: dict[str, Any]) -> random.Random:
    return random.Random(str(profile.get("id", "")))


async def simulate_week_for_profile(
    profile: dict[str, Any], week: int = 2
) -> dict[str, int]:
    """Write one week of inbound replies and post analytics for this creator."""
    rng = _seeded_random(profile)
    name = profile.get("name") or "there"
    median = int(profile.get("median_views") or 5000)

    opportunities = await db.list_opportunities()
    outreached = [
        o
        for o in opportunities
        if o.get("status") in ("outreached", "packaged", "engaged")
    ] or opportunities[:2]

    replies = 0
    for opp in outreached[:3]:
        label, subject_tpl, body_tpl = _REPLY_TEMPLATES[rng.randrange(len(_REPLY_TEMPLATES))]
        topic = opp.get("title", "your idea")
        await db.add_engagement_item(
            source="email",
            payload={
                "from": f"hello@{_slug(topic)}.example",
                "subject": subject_tpl.format(subject=topic),
                "body": body_tpl.format(name=name, topic=topic),
                "label_hint": label,
                "synthetic": True,
            },
            opportunity_id=opp.get("id"),
        )
        replies += 1

    # Post performance, biased so the adapt loop has a real signal to find:
    # topics the creator says work do well, the ones they say do not, do not.
    posts = 0
    best = list(profile.get("best_performing") or []) or ["your usual format"]
    worst = list(profile.get("worst_performing") or []) or ["an experiment"]
    plan = [(t, 2.0, 3.6) for t in best[:2]] + [(t, 0.25, 0.6) for t in worst[:1]]

    for index, (topic, low, high) in enumerate(plan):
        ratio = rng.uniform(low, high)
        views = int(median * ratio)
        await db.add_analytics_post(
            {
                "id": f"sim-w{week}-{_slug(topic)}",
                "title": f"{topic}".capitalize(),
                "topic": topic,
                "platform": (profile.get("platforms") or ["tiktok"])[0],
                "posted_at": (utcnow() - timedelta(days=6 - index * 2)).isoformat(),
                "week": week,
                "views": views,
                "saves": int(views * rng.uniform(0.02, 0.05)),
                "avg_watch_pct": round(min(95.0, 22 + ratio * 18), 1),
                "median_ratio": round(ratio, 2),
                "synthetic": True,
            }
        )
        posts += 1

    return {"replies": replies, "posts": posts, "week": week}


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-")[:40]
