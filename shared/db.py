"""Async engine and session factory. One database, every service.

    from shared.db import session

    async with session() as s:
        rows = (await s.scalars(select(Opportunity))).all()

The engine is built lazily on first use, not at import. Several modules import
this transitively (agents, tools, the MCP registry) in contexts that never
touch the database -- a module-level `create_async_engine` would make an
unreachable database an import error for all of them.

DATABASE_URL
------------
Postgres is the target:

    postgresql+asyncpg://creatorloop:creatorloop@localhost:5432/creatorloop

SQLite also works, for tests and for a local loop with no server running:

    sqlite+aiosqlite:///./creatorloop.db

The models in shared/models.py are written to run on both.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_URL = "postgresql+asyncpg://creatorloop:creatorloop@localhost:5432/creatorloop"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_URL)


def is_sqlite() -> bool:
    return database_url().startswith("sqlite")


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = database_url()
        kwargs: dict = {"echo": os.getenv("SQL_ECHO", "0") == "1", "future": True}
        if not url.startswith("sqlite"):
            # A campaign run holds a connection for minutes at a time, so the
            # pool has to outnumber concurrent runs or they queue behind each
            # other. pre_ping keeps a container restart from handing out dead
            # connections.
            kwargs |= {
                "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
                "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
                "pool_pre_ping": True,
                "pool_recycle": 1800,
            }
        _engine = create_async_engine(url, **kwargs)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            engine(),
            expire_on_commit=False,  # objects stay usable after the block exits
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """A transactional session. Commits on clean exit, rolls back on error."""
    async with sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def dispose() -> None:
    """Close the pool. Call from a service's shutdown hook."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def create_all() -> None:
    """Create tables directly from the models, skipping Alembic.

    For tests and throwaway local databases only. Anything shared -- including
    your own dev Postgres -- goes through `alembic upgrade head`, or the next
    person to pull cannot reproduce your schema.
    """
    from shared.models import Base

    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def healthcheck() -> dict:
    from sqlalchemy import text

    try:
        async with session() as s:
            await s.execute(text("SELECT 1"))
        return {"database": "ok", "url": _safe_url()}
    except Exception as exc:
        return {"database": "unreachable", "url": _safe_url(), "error": str(exc)}


def _safe_url() -> str:
    """The URL with the password removed, safe for /health and logs."""
    url = database_url()
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
