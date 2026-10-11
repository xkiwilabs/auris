from __future__ import annotations

import sqlite3

import aiosqlite
import pytest

from auris.voice_library import db


async def _columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {r["name"] for r in await cur.fetchall()}


async def _index_names(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA index_list({table})")
    return {r["name"] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_create_tables_makes_the_users_table():
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        assert {
            "id",
            "email",
            "password_hash",
            "password_version",
            "role",
            "must_change_password",
            "created_at",
            "disabled_at",
        } <= await _columns(conn, "users")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_email_is_unique_case_insensitively():
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        ins = "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, 'h', 'now')"
        await conn.execute(ins, ("1", "Alice@Example.com"))
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(ins, ("2", "alice@example.com"))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_adds_owner_id_to_a_pre_existing_api_keys_table():
    """The upgrade path: a database that already has the old api_keys shape."""
    conn = await db.connect(":memory:")
    try:
        await conn.executescript(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL, revoked_at TEXT, last_used_at TEXT
            );
            INSERT INTO api_keys (id, name, key_hash, created_at)
            VALUES ('k1', 'legacy-bot', 'hash', '2026-01-01T00:00:00+00:00');
            """
        )
        await conn.commit()
        assert "owner_id" not in await _columns(conn, "api_keys")

        await db.create_tables(conn)

        assert "owner_id" in await _columns(conn, "api_keys")
        cur = await conn.execute("SELECT name, owner_id FROM api_keys WHERE id = 'k1'")
        row = await cur.fetchone()
        # The pre-existing key survives, unrevoked and honestly unowned.
        assert row["name"] == "legacy-bot"
        assert row["owner_id"] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_create_tables_is_idempotent():
    """Every CLI command runs it on every invocation, so a second run must be a no-op."""
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        await db.create_tables(conn)
        await db.create_tables(conn)
        assert "owner_id" in await _columns(conn, "api_keys")
    finally:
        await conn.close()


async def _legacy_speakers_db(path) -> None:
    """A database shaped the way the PRE-migration code made it, with real rows.

    Written out by hand rather than imported from an old commit, because the point is to pin
    the exact starting shape: the global unique name index, and no owner/visibility columns.
    """
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE speakers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
            default_model TEXT NOT NULL DEFAULT 'f5_tts', default_speed REAL NOT NULL DEFAULT 1.0
        );
        CREATE TABLE reference_clips (
            id TEXT PRIMARY KEY,
            speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, transcript TEXT NOT NULL,
            duration_s REAL, created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_speakers_name ON speakers (name COLLATE NOCASE);
        """
    )
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, default_model, default_speed)"
        " VALUES ('s1', 'mike', '2026-01-01', 'f5_tts', 1.0)"
    )
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, default_model, default_speed)"
        " VALUES ('s2', 'narrator-clone', '2026-01-02', 'f5_tts', 1.2)"
    )
    await conn.execute(
        "INSERT INTO reference_clips (id, speaker_id, filename, transcript, duration_s,"
        " created_at) VALUES ('c1', 's1', 'clip_001.wav', 'hello there', 4.5, '2026-01-01')"
    )
    await conn.commit()
    await conn.close()


@pytest.mark.asyncio
async def test_migrating_a_populated_database_preserves_every_voice(tmp_path):
    """The production database holds live voices; losing one is unrecoverable."""
    path = tmp_path / "legacy.db"
    await _legacy_speakers_db(path)
    conn = await db.connect(path)
    try:
        await db.create_tables(conn)
        cur = await conn.execute(
            "SELECT id, name, owner_id, visibility, default_speed FROM speakers ORDER BY id"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        assert [r["name"] for r in rows] == ["mike", "narrator-clone"]
        # Legacy rows are unowned and shared: visible to all, mutable only by an admin.
        assert all(r["owner_id"] is None for r in rows)
        assert all(r["visibility"] == "shared" for r in rows)
        assert rows[1]["default_speed"] == 1.2  # unrelated columns untouched
        cur = await conn.execute("SELECT COUNT(*) AS n FROM reference_clips")
        assert (await cur.fetchone())["n"] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_the_global_name_index_is_replaced_by_a_per_owner_one(tmp_path):
    path = tmp_path / "legacy.db"
    await _legacy_speakers_db(path)
    conn = await db.connect(path)
    try:
        await db.create_tables(conn)
        names = await _index_names(conn, "speakers")
        assert "idx_speakers_name" not in names, "the global unique name index must be dropped"
        assert "idx_speakers_owner_name" in names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_two_owners_may_use_the_same_voice_name(tmp_path):
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        ins = (
            "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
            " VALUES (?, ?, 'now', ?, 'private')"
        )
        await conn.execute(ins, ("a", "narrator", "alice"))
        await conn.execute(ins, ("b", "narrator", "bob"))
        await conn.commit()
        # ...but the same owner may not use it twice, case-insensitively.
        with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError)):
            await conn.execute(ins, ("c", "NARRATOR", "alice"))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_migrated_schema_matches_a_freshly_created_one(tmp_path):
    """Divergence here is how a bug reaches only the machines that upgraded."""
    legacy = tmp_path / "legacy.db"
    await _legacy_speakers_db(legacy)
    migrated = await db.connect(legacy)
    fresh = await db.connect(tmp_path / "fresh.db")
    try:
        await db.create_tables(migrated)
        await db.create_tables(fresh)
        for table in ("speakers", "reference_clips"):
            cur_m = await migrated.execute(f"PRAGMA table_info({table})")
            cur_f = await fresh.execute(f"PRAGMA table_info({table})")
            cols_m = sorted(r["name"] for r in await cur_m.fetchall())
            cols_f = sorted(r["name"] for r in await cur_f.fetchall())
            assert cols_m == cols_f, table
        assert await _index_names(migrated, "speakers") == await _index_names(fresh, "speakers")
    finally:
        await migrated.close()
        await fresh.close()


@pytest.mark.asyncio
async def test_the_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    await _legacy_speakers_db(path)
    conn = await db.connect(path)
    try:
        for _ in range(4):
            await db.create_tables(conn)
        cur = await conn.execute("SELECT COUNT(*) AS n FROM speakers")
        assert (await cur.fetchone())["n"] == 2
        assert "idx_speakers_owner_name" in await _index_names(conn, "speakers")
    finally:
        await conn.close()


async def _intermediate_commit_db(path) -> None:
    """A database shaped the way an *intermediate* commit of the ownership branch made it: the
    owner/visibility columns already exist, so `migrate()`'s `if "visibility" not in ...` branch
    will never run again — and a voice cloned on the box while that commit was deployed is
    sitting there unowned and stored as 'private'.
    """
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE speakers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
            default_model TEXT NOT NULL DEFAULT 'f5_tts', default_speed REAL NOT NULL DEFAULT 1.0,
            owner_id TEXT, visibility TEXT NOT NULL DEFAULT 'private'
        );
        CREATE TABLE reference_clips (
            id TEXT PRIMARY KEY,
            speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, transcript TEXT NOT NULL,
            duration_s REAL, created_at TEXT NOT NULL
        );
        """
    )
    ins = (
        "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
        " VALUES (?, ?, '2026-01-01', ?, ?)"
    )
    await conn.execute(ins, ("s1", "box-created", None, "private"))  # the dishonest row
    await conn.execute(ins, ("s2", "legacy", None, "shared"))  # already correct
    await conn.execute(ins, ("s3", "alices", "alice", "private"))  # owned: must stay private
    await conn.commit()
    await conn.close()


@pytest.mark.asyncio
async def test_the_migration_repairs_an_unowned_private_row(tmp_path):
    """An unowned row is read by every account whatever its visibility column says, so a stored
    'private' is a lie that `auris voices adopt` would then turn into a silent revocation. The
    repair sits in the unconditional part of migrate(): the column-adding branch has already
    run on such a database and will never run again.
    """
    path = tmp_path / "intermediate.db"
    await _intermediate_commit_db(path)
    conn = await db.connect(path)
    try:
        await db.create_tables(conn)
        cur = await conn.execute("SELECT id, owner_id, visibility FROM speakers ORDER BY id")
        rows = {r["id"]: (r["owner_id"], r["visibility"]) for r in await cur.fetchall()}
        assert rows["s1"] == (None, "shared"), "the dishonest row must be repaired"
        assert rows["s2"] == (None, "shared")
        assert rows["s3"] == ("alice", "private"), "an owned voice keeps its visibility"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_the_unowned_visibility_repair_is_idempotent(tmp_path):
    """Every CLI command runs migrate(); a second pass must match no rows and change nothing."""
    path = tmp_path / "intermediate.db"
    await _intermediate_commit_db(path)
    conn = await db.connect(path)
    try:
        await db.create_tables(conn)
        cur = await conn.execute("SELECT id, owner_id, visibility FROM speakers ORDER BY id")
        first = [tuple(r) for r in await cur.fetchall()]
        for _ in range(3):
            await db.create_tables(conn)
        cur = await conn.execute("SELECT id, owner_id, visibility FROM speakers ORDER BY id")
        assert [tuple(r) for r in await cur.fetchall()] == first
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_two_unowned_voices_cannot_share_a_name(tmp_path):
    """Unowned voices cannot share a name, but owned voices can.

    This test pins the behaviour of the partial unique index idx_speakers_unowned_name,
    which exists because SQLite treats NULL as distinct in unique indexes. The composite
    index (owner_id, name) alone would let two unowned rows (both with owner_id IS NULL)
    share the same name, but the local CLI keeps creating unowned voices, so a partial
    index is needed to enforce uniqueness among them while allowing multiple owners to
    share names.
    """
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        # Two unowned voices cannot share the same name, case-insensitively.
        await conn.execute(
            "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
            " VALUES ('a', 'narrator', 'now', NULL, 'shared')"
        )
        with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError)):
            await conn.execute(
                "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
                " VALUES ('b', 'NARRATOR', 'now', NULL, 'shared')"
            )
        # But two owned voices CAN share the same name if they have different owners.
        await conn.execute(
            "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
            " VALUES ('c', 'narrator', 'now', 'alice', 'private')"
        )
        await conn.execute(
            "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
            " VALUES ('d', 'narrator', 'now', 'bob', 'private')"
        )
        await conn.commit()
        # Verify the rows exist.
        cur = await conn.execute("SELECT COUNT(*) AS n FROM speakers")
        assert (await cur.fetchone())["n"] == 3
    finally:
        await conn.close()
