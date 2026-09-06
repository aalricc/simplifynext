"""Accounts, sessions, and the checks that keep one account out of another."""

from __future__ import annotations

import pytest
import pytest_asyncio

# DATABASE_URL is set by tests/conftest.py before this imports shared.db.
# Never setdefault it here: the suite drops every table, and an exported
# dev URL would be the thing it dropped.

from shared.db import create_all, engine  # noqa: E402
from shared.models import Base  # noqa: E402
from ui_client import auth  # noqa: E402

GOOD_PASSWORD = "a-long-enough-password"


@pytest_asyncio.fixture
async def clean_db():
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await create_all()
    yield


@pytest.mark.asyncio
async def test_signup_then_login(clean_db):
    user = await auth.signup("Person@Example.com ", GOOD_PASSWORD, "Person")
    # Email is normalised, so casing cannot create a second account.
    assert user.email == "person@example.com"

    same = await auth.login("PERSON@example.com", GOOD_PASSWORD)
    assert same.id == user.id


@pytest.mark.asyncio
async def test_password_is_never_stored_in_the_clear(clean_db):
    user = await auth.signup("p@example.com", GOOD_PASSWORD)
    assert GOOD_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")


@pytest.mark.asyncio
async def test_duplicate_email_is_refused(clean_db):
    await auth.signup("dup@example.com", GOOD_PASSWORD)
    with pytest.raises(auth.AuthError) as exc:
        await auth.signup("dup@example.com", GOOD_PASSWORD)
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_short_password_is_refused(clean_db):
    with pytest.raises(auth.AuthError):
        await auth.signup("short@example.com", "abc")


@pytest.mark.asyncio
async def test_wrong_password_and_unknown_email_look_identical(clean_db):
    """Neither response may reveal whether the account exists."""
    await auth.signup("real@example.com", GOOD_PASSWORD)

    with pytest.raises(auth.AuthError) as wrong:
        await auth.login("real@example.com", "the-wrong-password")
    with pytest.raises(auth.AuthError) as missing:
        await auth.login("ghost@example.com", "the-wrong-password")

    assert str(wrong.value) == str(missing.value)
    assert wrong.value.status == missing.value.status == 401


@pytest.mark.asyncio
async def test_login_is_rate_limited(clean_db):
    await auth.signup("rl@example.com", GOOD_PASSWORD)
    for _ in range(auth.MAX_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            await auth.login("rl@example.com", "bad-password-here")

    # Even the correct password is refused while the window is open.
    with pytest.raises(auth.AuthError) as exc:
        await auth.login("rl@example.com", GOOD_PASSWORD)
    assert exc.value.status == 429


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_round_trip_and_revocation(clean_db):
    user = await auth.signup("s@example.com", GOOD_PASSWORD)
    token = await auth.create_session(user.id)

    assert (await auth.resolve_session(token)).id == user.id

    await auth.revoke_session(token)
    assert await auth.resolve_session(token) is None


@pytest.mark.asyncio
async def test_only_the_hash_of_a_session_is_stored(clean_db):
    from sqlalchemy import select

    from shared.db import session
    from shared.models import Session

    user = await auth.signup("h@example.com", GOOD_PASSWORD)
    token = await auth.create_session(user.id)

    async with session() as s:
        rows = (await s.scalars(select(Session))).all()
    assert len(rows) == 1
    assert rows[0].token_hash != token  # the cookie itself never lands in the DB


@pytest.mark.asyncio
async def test_garbage_session_resolves_to_nobody(clean_db):
    for bad in (None, "", "not-a-real-token"):
        assert await auth.resolve_session(bad) is None


@pytest.mark.asyncio
async def test_revoke_all_sessions(clean_db):
    user = await auth.signup("many@example.com", GOOD_PASSWORD)
    tokens = [await auth.create_session(user.id) for _ in range(3)]

    await auth.revoke_all_sessions(user.id)
    for token in tokens:
        assert await auth.resolve_session(token) is None


# ---------------------------------------------------------------------------
# profile ownership -- the check that stops account A naming account B's profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_refuses_another_users_profile(clean_db):
    alice = await auth.signup("alice@example.com", GOOD_PASSWORD)
    bob = await auth.signup("bob@example.com", GOOD_PASSWORD)

    alice_profile = await auth.create_profile(alice.id, {"handle": "alice", "city": "X"})

    assert await auth.get_profile(alice.id, str(alice_profile.id)) is not None
    # Bob naming Alice's real profile id gets nothing.
    assert await auth.get_profile(bob.id, str(alice_profile.id)) is None


@pytest.mark.asyncio
async def test_get_profile_handles_malformed_ids(clean_db):
    user = await auth.signup("m@example.com", GOOD_PASSWORD)
    for bad in ("", "not-a-uuid", "../../etc/passwd"):
        assert await auth.get_profile(user.id, bad) is None


@pytest.mark.asyncio
async def test_duplicate_handle_for_one_user_is_refused(clean_db):
    user = await auth.signup("dh@example.com", GOOD_PASSWORD)
    await auth.create_profile(user.id, {"handle": "same"})
    with pytest.raises(auth.AuthError):
        await auth.create_profile(user.id, {"handle": "same"})


@pytest.mark.asyncio
async def test_two_users_may_share_a_handle(clean_db):
    """Handles are unique per user, not globally -- nobody squats a name."""
    a = await auth.signup("a2@example.com", GOOD_PASSWORD)
    b = await auth.signup("b2@example.com", GOOD_PASSWORD)
    assert await auth.create_profile(a.id, {"handle": "cooks"})
    assert await auth.create_profile(b.id, {"handle": "cooks"})
