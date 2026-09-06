"""Feature flags from .env. Read these, do not scatter os.getenv across services."""

from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


def use_fixtures() -> bool:
    """Return canned agent output instead of calling the model.

    Defaults OFF. It used to default on, which meant every agent returned
    Maya's pre-written answers -- correct for the demo, wrong for every real
    account. Keep it as a test/CI switch, not a runtime mode.
    """
    return _flag("USE_FIXTURES", "0")


def allow_seed_data() -> bool:
    """Unlock `demo/maya` seed data. Tests and the demo seed script only."""
    return _flag("CREATORLOOP_ALLOW_SEED", "0")


def is_production() -> bool:
    return os.getenv("CREATORLOOP_ENV", "dev").strip().lower() == "production"


def pause_before_send() -> bool:
    """Human-in-the-loop gate before outreach goes out. Demo default: off."""
    return _flag("PAUSE_BEFORE_SEND", "0")


def daily_run_cap() -> int:
    """Max campaign runs per profile per day. 0 disables the cap.

    A full campaign is 60-80 model calls. On a shared key with real signups
    that is the line item that actually costs money, so the cap exists before
    the first surprise invoice rather than after it.
    """
    try:
        return int(os.getenv("DAILY_RUN_CAP", "20"))
    except ValueError:
        return 20
