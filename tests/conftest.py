"""Test database isolation.

THE TESTS DROP EVERY TABLE. That is fine against a scratch database and
catastrophic against anything else, so this module decides which database the
suite talks to before any test module imports `shared.db` -- and refuses to run
against one that does not look disposable.

The failure this prevents is real and easy to hit: the fixtures used
`os.environ.setdefault("DATABASE_URL", ...)`, which does nothing when the
variable is already exported. Anyone with their dev database in the environment
(`export DATABASE_URL=...` then `pytest`) had it silently wiped.

Override with CREATORLOOP_TEST_DATABASE_URL if you want the suite to run
somewhere specific -- it still has to pass the safety check below.
"""

from __future__ import annotations

import os
import pathlib

import pytest

DEFAULT_TEST_DB = "sqlite+aiosqlite:///./test_creatorloop.db"


def _looks_disposable(url: str) -> bool:
    """A URL the suite is allowed to drop every table in."""
    lowered = url.lower()
    if lowered.startswith("sqlite") and "test" in lowered:
        return True
    # A server-backed database must name itself a test database, so a stray
    # production URL can never satisfy this by accident.
    return "test" in lowered.rsplit("/", 1)[-1]


def pytest_configure(config: pytest.Config) -> None:
    requested = os.environ.get("CREATORLOOP_TEST_DATABASE_URL")
    url = requested or DEFAULT_TEST_DB

    if not _looks_disposable(url):
        raise pytest.UsageError(
            f"Refusing to run the test suite against {url!r}.\n"
            "These tests DROP EVERY TABLE. Point "
            "CREATORLOOP_TEST_DATABASE_URL at a database whose name contains "
            "'test', or unset it to use the default SQLite file."
        )

    # Set, never setdefault: an exported DATABASE_URL from the developer's
    # shell must not decide where the tests write.
    os.environ["DATABASE_URL"] = url
    # Agents must not reach the network during tests.
    os.environ.setdefault("USE_FIXTURES", "1")


def pytest_unconfigure(config: pytest.Config) -> None:
    if os.environ.get("DATABASE_URL", "").endswith("test_creatorloop.db"):
        pathlib.Path("test_creatorloop.db").unlink(missing_ok=True)
