"""Tenant isolation. These are the tests that matter most in this repo.

Every other test can pass while these fail and the app is still broken in the
one way that would be unacceptable: showing one creator another creator's data.

    pytest tests/ -q

Runs on SQLite so it needs no server.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

# DATABASE_URL is set by tests/conftest.py before this imports shared.db.
# Never setdefault it here: the suite drops every table, and an exported
# dev URL would be the thing it dropped.

from shared import tenant  # noqa: E402
from shared.db import create_all, engine, session  # noqa: E402
from shared.models import Base, CreatorProfile, User  # noqa: E402

from pipeline_manager import db as pdb  # noqa: E402


@pytest_asyncio.fixture
async def profiles():
    """Two creators owned by two different users."""
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await create_all()

    made = []
    async with session() as s:
        for email, handle in (("a@example.com", "alice"), ("b@example.com", "bob")):
            user = User(email=email, password_hash="x")
            s.add(user)
            await s.flush()
            profile = CreatorProfile(
                user_id=user.id, handle=handle, display_name=handle, city="X", niche="y"
            )
            s.add(profile)
            await s.flush()
            made.append(str(profile.id))
    yield made


@pytest.mark.asyncio
async def test_same_opportunity_id_does_not_collide(profiles):
    """The exact case that broke: ids are only unique within a profile."""
    alice, bob = profiles

    tenant.set_profile(alice)
    await pdb.upsert_opportunity(
        {"id": "opp-laksa", "title": "ALICE laksa", "type": "trend", "score": 50}
    )
    tenant.set_profile(bob)
    await pdb.upsert_opportunity(
        {"id": "opp-laksa", "title": "BOB laksa", "type": "trend", "score": 90}
    )

    tenant.set_profile(alice)
    assert [o["title"] for o in await pdb.list_opportunities()] == ["ALICE laksa"]
    assert (await pdb.get_opportunity("opp-laksa"))["title"] == "ALICE laksa"

    tenant.set_profile(bob)
    assert [o["title"] for o in await pdb.list_opportunities()] == ["BOB laksa"]


@pytest.mark.asyncio
async def test_status_update_does_not_cross_tenants(profiles):
    alice, bob = profiles
    for pid, title in ((alice, "A"), (bob, "B")):
        tenant.set_profile(pid)
        await pdb.upsert_opportunity(
            {"id": "shared-id", "title": title, "type": "trend", "score": 10}
        )

    tenant.set_profile(alice)
    await pdb.update_status("shared-id", "won")

    tenant.set_profile(bob)
    assert (await pdb.get_opportunity("shared-id"))["status"] == "new"


@pytest.mark.asyncio
async def test_memory_is_per_profile(profiles):
    """The old table was CHECK (id = 1) -- one row for the whole world."""
    alice, bob = profiles

    tenant.set_profile(alice)
    await pdb.write_memory({"wins": ["alice win"], "losses": [], "next_bias": []})
    tenant.set_profile(bob)
    await pdb.write_memory({"wins": ["bob win"], "losses": [], "next_bias": []})

    tenant.set_profile(alice)
    assert (await pdb.get_memory())["wins"] == ["alice win"]
    tenant.set_profile(bob)
    assert (await pdb.get_memory())["wins"] == ["bob win"]


@pytest.mark.asyncio
async def test_rag_inbox_and_analytics_are_scoped(profiles):
    alice, bob = profiles

    tenant.set_profile(alice)
    await pdb.add_rag_document({"title": "alice note", "text": "laksa"})
    await pdb.add_engagement_item("email", {"subject": "for alice"})
    await pdb.add_analytics_post({"title": "alice post", "views": 100})

    tenant.set_profile(bob)
    assert await pdb.list_rag_documents() == []
    assert await pdb.list_engagement_items() == []
    assert await pdb.list_analytics_posts() == []

    tenant.set_profile(alice)
    assert len(await pdb.list_rag_documents()) == 1
    assert len(await pdb.list_engagement_items()) == 1
    assert len(await pdb.list_analytics_posts()) == 1


@pytest.mark.asyncio
async def test_missing_tenant_raises_rather_than_reading(profiles):
    """A query with no tenant must fail loudly, never return someone's rows."""
    alice, _ = profiles
    tenant.set_profile(alice)
    await pdb.upsert_opportunity({"id": "x", "title": "t", "type": "trend", "score": 1})

    tenant.set_profile("")
    for call in (
        pdb.list_opportunities(),
        pdb.get_memory(),
        pdb.list_rag_documents(),
        pdb.list_engagement_items(),
    ):
        with pytest.raises(tenant.TenantError):
            await call


@pytest.mark.asyncio
async def test_calendar_events_are_scoped(profiles):
    alice, bob = profiles
    event = {
        "opportunity_id": "opp-1",
        "slot": "2026-09-10T18:00:00+08:00",
        "kind": "post",
        "title": "Post",
    }
    tenant.set_profile(alice)
    await pdb.save_calendar_event(event)
    tenant.set_profile(bob)
    assert await pdb.list_calendar_events() == []
    tenant.set_profile(alice)
    assert len(await pdb.list_calendar_events()) == 1


# ---------------------------------------------------------------------------
# the signed internal header
# ---------------------------------------------------------------------------


def test_token_round_trip():
    pid = str(uuid.uuid4())
    assert tenant.verify_token(pid, tenant.mint_token(pid))


def test_token_is_bound_to_its_profile():
    """A token minted for one profile must not authorise another."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert not tenant.verify_token(b, tenant.mint_token(a))


def test_expired_and_malformed_tokens_are_rejected():
    pid = str(uuid.uuid4())
    old = tenant.mint_token(pid, now=0)  # expired in 1970
    assert not tenant.verify_token(pid, old)
    for bad in ("", "garbage", "not-a-number.abc", "9999999999."):
        assert not tenant.verify_token(pid, bad)


def test_outbound_headers_carry_no_credentials_without_a_profile():
    """No profile means no tenant identity on the wire.

    The persona header may still be present -- it only selects which checked-in
    demo fixtures to serve and authorises nothing -- so assert on the two
    headers that actually grant access.
    """
    tenant.set_profile("")
    headers = tenant.outbound_headers()
    assert tenant.PROFILE_HEADER not in headers
    assert tenant.TOKEN_HEADER not in headers


def test_persona_header_is_absent_when_not_running_fixtures(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "0")
    tenant.set_profile(str(uuid.uuid4()))
    assert tenant.PERSONA_HEADER not in tenant.outbound_headers()
