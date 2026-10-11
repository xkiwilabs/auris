from __future__ import annotations

import pytest
import pytest_asyncio

from auris.voice_library import db, keys
from auris.voice_library.exceptions import NameTaken


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


def test_generate_key_has_prefix_and_entropy():
    a, b = keys.generate_key(), keys.generate_key()
    assert a.startswith(keys.KEY_PREFIX) and b.startswith(keys.KEY_PREFIX)
    assert a != b
    assert len(a) > 40


def test_hash_key_is_sha256_hex():
    assert keys.hash_key("x") == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_only_the_hash(conn):
    record, plaintext = await keys.create_key(conn, "alice")
    assert record.name == "alice" and record.revoked_at is None
    assert plaintext.startswith("auris_")
    cur = await conn.execute("SELECT key_hash FROM api_keys WHERE id = ?", (record.id,))
    (stored,) = await cur.fetchone()
    assert stored == keys.hash_key(plaintext)
    # No column of the row may carry the plaintext, in whole or in part.
    cur = await conn.execute("SELECT * FROM api_keys WHERE id = ?", (record.id,))
    row = await cur.fetchone()
    assert all(plaintext not in str(value) for value in tuple(row))


@pytest.mark.asyncio
async def test_lookup_active_finds_by_plaintext(conn):
    record, plaintext = await keys.create_key(conn, "alice")
    found = await keys.lookup_active(conn, plaintext)
    assert found is not None and found.id == record.id
    assert await keys.lookup_active(conn, "auris_not-a-key") is None


@pytest.mark.asyncio
async def test_revoke_hides_key_from_lookup_but_keeps_the_row(conn):
    _, plaintext = await keys.create_key(conn, "alice")
    revoked = await keys.revoke_key(conn, "alice")
    assert revoked is not None and revoked.revoked_at is not None
    assert await keys.lookup_active(conn, plaintext) is None
    assert [k.name for k in await keys.list_keys(conn)] == ["alice"]  # audit row survives


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_none_for_unknown(conn):
    await keys.create_key(conn, "alice")
    first = await keys.revoke_key(conn, "alice")
    second = await keys.revoke_key(conn, "alice")
    assert first.revoked_at == second.revoked_at
    assert await keys.revoke_key(conn, "nobody") is None


@pytest.mark.asyncio
async def test_names_are_unique_case_insensitively_even_after_revoke(conn):
    await keys.create_key(conn, "alice")
    with pytest.raises(NameTaken):
        await keys.create_key(conn, "Alice")
    await keys.revoke_key(conn, "alice")
    with pytest.raises(NameTaken):  # spec: a revoked name is not re-issued
        await keys.create_key(conn, "alice")


@pytest.mark.asyncio
async def test_blank_name_is_rejected(conn):
    with pytest.raises(ValueError):
        await keys.create_key(conn, "   ")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["admin", "Admin", "  ADMIN  "])
async def test_admin_is_reserved_for_the_master_token(conn, name):
    with pytest.raises(NameTaken):
        await keys.create_key(conn, name)
    assert await keys.list_keys(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "slash/name",  # would be unrevokable over HTTP: DELETE /v1/keys/{name} cannot match it
        "with space",
        "quote'name",
        "nl\nline",  # a name that could forge extra audit log lines
        "ansi\x1b[31m",
        "x" * 65,
    ],
)
async def test_unsafe_names_are_rejected(conn, name):
    with pytest.raises(ValueError):
        await keys.create_key(conn, name)
    assert await keys.list_keys(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["alice", "ci-bot", "svc_1", "build.2", "A" * 64])
async def test_ordinary_names_are_accepted(conn, name):
    record, _ = await keys.create_key(conn, name)
    assert record.name == name


@pytest.mark.asyncio
async def test_name_is_stripped_before_it_is_stored(conn):
    record, _ = await keys.create_key(conn, "  alice  ")
    assert record.name == "alice"
    assert await keys.get_key_by_name(conn, "alice") is not None
    with pytest.raises(NameTaken):
        await keys.create_key(conn, "alice")


@pytest.mark.asyncio
async def test_a_rejected_duplicate_leaves_no_open_transaction(conn):
    """The server shares one long-lived connection: a 409 must not strand the write lock."""
    await keys.create_key(conn, "alice")
    with pytest.raises(NameTaken):
        await keys.create_key(conn, "Alice")
    assert conn._conn.in_transaction is False
    # The connection is still usable for both reads and writes.
    record, _ = await keys.create_key(conn, "bob")
    assert [k.name for k in await keys.list_keys(conn)] == ["alice", "bob"]
    assert record.name == "bob"


@pytest.mark.asyncio
async def test_list_and_lookup_never_expose_the_hash(conn):
    _, plaintext = await keys.create_key(conn, "alice")
    for record in (*await keys.list_keys(conn), await keys.lookup_active(conn, plaintext)):
        assert not hasattr(record, "key_hash")
        assert keys.hash_key(plaintext) not in repr(record)


@pytest.mark.asyncio
async def test_touch_last_used_sets_a_timestamp(conn):
    record, _ = await keys.create_key(conn, "alice")
    assert record.last_used_at is None
    await keys.touch_last_used(conn, record.id)
    again = await keys.get_key_by_name(conn, "alice")
    assert again.last_used_at is not None


@pytest.mark.asyncio
async def test_list_keys_filters_by_owner(conn):
    alice_key, _ = await keys.create_key(conn, "alices-bot", owner_id="alice-id")
    await keys.create_key(conn, "bobs-bot", owner_id="bob-id")
    await keys.create_key(conn, "legacy-bot")  # unowned

    only_alices = await keys.list_keys(conn, owner_id="alice-id")
    assert [k.name for k in only_alices] == [alice_key.name]
    assert only_alices[0].owner_id == "alice-id"


@pytest.mark.asyncio
async def test_list_keys_with_no_owner_filter_returns_everything_including_unowned(conn):
    await keys.create_key(conn, "alices-bot", owner_id="alice-id")
    await keys.create_key(conn, "bobs-bot", owner_id="bob-id")
    await keys.create_key(conn, "legacy-bot")  # unowned

    everyone = await keys.list_keys(conn)
    assert {k.name for k in everyone} == {"alices-bot", "bobs-bot", "legacy-bot"}
    by_name = {k.name: k.owner_id for k in everyone}
    assert by_name["alices-bot"] == "alice-id"
    assert by_name["bobs-bot"] == "bob-id"
    assert by_name["legacy-bot"] is None
