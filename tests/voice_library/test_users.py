from __future__ import annotations

import asyncio
import threading

import pytest
import pytest_asyncio

from auris.voice_library import db, users
from auris.voice_library.exceptions import LastActiveAdmin, NameTaken

GOOD = "a-good-long-password"


# `@pytest_asyncio.fixture`, NOT `@pytest.fixture`: asyncio_mode = "strict" (pyproject.toml), so a
# plain @pytest.fixture around an async generator hands the test the generator object instead of a
# connection. This is the convention in tests/voice_library/test_keys.py — follow it.
@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_create_then_fetch_by_email_is_case_insensitive(conn):
    made = await users.create_user(conn, "Alice@Example.com", GOOD)
    assert made.email == "Alice@Example.com"  # stored as typed
    assert made.role == users.USER_ROLE
    assert made.must_change_password is True  # admin-provisioned accounts must change
    assert made.password_version == 1
    found = await users.get_user_by_email(conn, "ALICE@example.COM")
    assert found is not None and found.id == made.id


@pytest.mark.asyncio
async def test_a_duplicate_email_is_a_name_taken(conn):
    await users.create_user(conn, "alice@example.com", GOOD)
    with pytest.raises(NameTaken, match="already"):
        await users.create_user(conn, "Alice@Example.com", GOOD)


@pytest.mark.asyncio
async def test_the_connection_is_usable_after_a_duplicate(conn):
    """create_key rolls back for this reason: the server shares one long-lived connection, so a
    failed INSERT that left the implicit transaction open would hold the WAL write lock."""
    await users.create_user(conn, "alice@example.com", GOOD)
    with pytest.raises(NameTaken):
        await users.create_user(conn, "alice@example.com", GOOD)
    await users.create_user(conn, "bob@example.com", GOOD)  # must not deadlock or fail
    assert len(await users.list_users(conn)) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "nobody",
        "no@domain",
        "a@b",
        "a b@example.com",
        "a@exa mple.com",
        "a/b@example.com",
        "x" * 250 + "@example.com",
        "ali\x00ce@example.com",  # NUL
        "ali\x1bce@example.com",  # ESC — would forge audit-log lines
        "ali\x7fce@example.com",  # DEL
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_email_is_refused(conn, bad):
    with pytest.raises(ValueError):
        await users.create_user(conn, bad, GOOD)


@pytest.mark.asyncio
async def test_an_invalid_role_is_refused(conn):
    with pytest.raises(ValueError, match="role"):
        await users.create_user(conn, "alice@example.com", GOOD, role="superuser")


@pytest.mark.asyncio
async def test_a_short_password_is_refused(conn):
    with pytest.raises(ValueError, match="at least 12"):
        await users.create_user(conn, "alice@example.com", "short")


@pytest.mark.asyncio
async def test_verify_login_accepts_the_right_password(conn):
    made = await users.create_user(conn, "alice@example.com", GOOD)
    got = await users.verify_login(conn, "ALICE@example.com", GOOD)
    assert got is not None and got.id == made.id


@pytest.mark.asyncio
async def test_verify_login_rejects_a_wrong_password_an_unknown_email_and_a_disabled_account(conn):
    await users.create_user(conn, "alice@example.com", GOOD)
    assert await users.verify_login(conn, "alice@example.com", "wrong-password-x") is None
    assert await users.verify_login(conn, "nobody@example.com", GOOD) is None
    await users.set_disabled(conn, "alice@example.com", True)
    assert await users.verify_login(conn, "alice@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_set_password_bumps_the_version_and_clears_the_change_flag(conn):
    made = await users.create_user(conn, "alice@example.com", GOOD)
    assert made.must_change_password is True
    after = await users.set_password(conn, made.id, "another-long-password")
    assert after.password_version == made.password_version + 1
    assert after.must_change_password is False
    assert await users.verify_login(conn, "alice@example.com", "another-long-password")
    assert await users.verify_login(conn, "alice@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_a_reset_sets_the_change_flag_again(conn):
    made = await users.create_user(conn, "alice@example.com", GOOD)
    await users.set_password(conn, made.id, "chosen-by-the-user")
    after = await users.set_password(conn, made.id, "reset-by-the-admin", must_change=True)
    assert after.must_change_password is True


@pytest.mark.asyncio
async def test_enable_clears_disabled_at(conn):
    await users.create_user(conn, "alice@example.com", GOOD)
    assert (await users.set_disabled(conn, "alice@example.com", True)).disabled_at is not None
    assert (await users.set_disabled(conn, "alice@example.com", False)).disabled_at is None


@pytest.mark.asyncio
async def test_set_disabled_on_an_unknown_email_raises_lookup_error(conn):
    with pytest.raises(LookupError):
        await users.set_disabled(conn, "nobody@example.com", True)


@pytest.mark.asyncio
async def test_count_active_admins_ignores_disabled_admins_and_plain_users(conn):
    await users.create_user(conn, "a@example.com", GOOD, role=users.ADMIN_ROLE)
    await users.create_user(conn, "b@example.com", GOOD, role=users.ADMIN_ROLE)
    await users.create_user(conn, "c@example.com", GOOD, role=users.USER_ROLE)
    assert await users.count_active_admins(conn) == 2
    await users.set_disabled(conn, "b@example.com", True)
    assert await users.count_active_admins(conn) == 1


@pytest.mark.asyncio
async def test_two_concurrent_disables_cannot_remove_the_last_two_admins(conn):
    await users.create_user(
        conn, "boss@example.com", GOOD, role="admin", must_change_password=False
    )
    await users.create_user(
        conn, "deputy@example.com", GOOD, role="admin", must_change_password=False
    )
    results = await asyncio.gather(
        users.set_disabled(conn, "boss@example.com", True, protect_last_admin=True),
        users.set_disabled(conn, "deputy@example.com", True, protect_last_admin=True),
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, LastActiveAdmin)]
    assert len(refused) == 1, results
    assert await users.count_active_admins(conn) == 1


@pytest.mark.asyncio
async def test_an_unknown_email_costs_the_same_one_derivation_as_a_wrong_password(
    conn, monkeypatch
):
    """Two derivations on one branch and one on the other is an enumeration oracle. Counted
    rather than timed: a wall-clock assertion would be flaky."""
    from auris.voice_library import passwords

    await users.create_user(conn, "alice@example.com", GOOD)
    users._dummy_hash()  # prime the cache so it is not counted below
    calls = []
    real = passwords.verify_password
    monkeypatch.setattr(
        passwords,
        "verify_password",
        lambda pw, stored: (calls.append(1), real(pw, stored))[1],
    )
    monkeypatch.setattr(
        passwords,
        "hash_password",
        lambda *a, **k: pytest.fail("no fresh derivation on the sign-in path"),
    )
    await users.verify_login(conn, "nobody@example.com", GOOD)
    unknown = len(calls)
    calls.clear()
    await users.verify_login(conn, "alice@example.com", "wrong-password-x")
    assert unknown == len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "email", ["alice@example.com", "nobody@example.com"], ids=["known", "unknown"]
)
async def test_the_scrypt_derivation_never_runs_on_the_event_loop(conn, monkeypatch, email):
    """Both sign-in branches must derive in a worker thread, not on the event loop.

    scrypt is ~42 ms of synchronous C and `POST /auth/login` needs no credential, so a derivation
    left on the loop lets an unauthenticated caller stall every other request on the server:
    measured at ~870 ms of stall for 20 concurrent attempts, against ~9 ms once off the loop.

    Asserting the *thread* rather than wall-clock latency on purpose — a timing assertion here
    would be flaky on a loaded box, while the thread identity is exact. Both branches are
    parametrised because the unknown-email branch is a separate call site and is the one an
    attacker reaches without knowing any address.
    """
    from auris.voice_library import passwords

    await users.create_user(conn, "alice@example.com", GOOD)
    users._dummy_hash()  # prime the cache; its own derivation is not what is under test
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = passwords.verify_password
    monkeypatch.setattr(
        passwords,
        "verify_password",
        lambda pw, stored: (seen.append(threading.get_ident()), real(pw, stored))[1],
    )

    await users.verify_login(conn, email, "the-wrong-password")

    assert seen, "verify_password was never called, so this test proves nothing"
    assert loop_thread not in seen, (
        "scrypt ran on the event loop thread; wrap it in asyncio.to_thread (see verify_login)"
    )
