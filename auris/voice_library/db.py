from __future__ import annotations

from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS speakers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    default_model TEXT NOT NULL DEFAULT 'f5_tts',
    default_speed REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS reference_clips (
    id          TEXT PRIMARY KEY,
    speaker_id  TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    transcript  TEXT NOT NULL,
    duration_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT,
    last_used_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_name ON api_keys (name COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash);

CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    email                TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    password_version     INTEGER NOT NULL DEFAULT 1,
    role                 TEXT NOT NULL DEFAULT 'user',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    disabled_at          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email COLLATE NOCASE);
"""


async def connect(db_path: str | Path) -> aiosqlite.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    if str(db_path) != ":memory:":
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
    return conn


async def _column_names(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cur.fetchall()}


async def migrate(conn: aiosqlite.Connection) -> None:
    """Additive column migrations for tables that may already hold data.

    `CREATE TABLE IF NOT EXISTS` in `_SCHEMA` cannot add a column to an existing table, so a
    database created before a column existed needs this. Guarded by PRAGMA table_info rather
    than a version counter: idempotent, and it also repairs a database that was created at
    some in-between commit.
    """
    if "owner_id" not in await _column_names(conn, "api_keys"):
        # No REFERENCES clause: SQLite's ALTER TABLE ADD COLUMN cannot add a column with a
        # foreign key to a populated table. The relationship is enforced in users.py/keys.py.
        await conn.execute("ALTER TABLE api_keys ADD COLUMN owner_id TEXT")
        await conn.commit()

    speaker_columns = await _column_names(conn, "speakers")
    if "owner_id" not in speaker_columns:
        # No REFERENCES clause: SQLite cannot add a foreign key to a populated table (same
        # reason as api_keys.owner_id above). The relationship is enforced in speakers.py.
        await conn.execute("ALTER TABLE speakers ADD COLUMN owner_id TEXT")
        await conn.commit()
    if "visibility" not in speaker_columns:
        # Existing voices predate accounts, so they become shared-and-unowned: everyone can
        # still use them and nothing breaks on upgrade, but only an admin can modify them
        # until `auris voices adopt` assigns them. A new row's default is derived from its
        # owner (speakers.default_visibility): 'private' when owned, 'shared' when not.
        await conn.execute(
            "ALTER TABLE speakers ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"
        )
        await conn.execute("UPDATE speakers SET visibility = 'shared'")
        await conn.commit()
    if "blend" not in speaker_columns:
        # A blended voice's recipe (voice_library/blends.py), JSON; NULL for a cloned voice.
        await conn.execute("ALTER TABLE speakers ADD COLUMN blend TEXT")
        await conn.commit()
    # Unconditional, not part of the branch above: an earlier commit of the ownership work
    # defaulted every new row to 'private', so a box that cloned a voice on the box (no owner
    # to attribute it to) already holds an unowned 'private' row — and that branch will never
    # run again for it. Such a row is read by every account whatever the column says, and
    # `auris voices adopt` would then silently revoke that access, so repair it here. This is
    # the "also repairs a database created at some in-between commit" property above, and it
    # is the storage-level half of speakers.UNOWNED_IS_ALWAYS_SHARED. Idempotent: a repaired
    # database matches no rows on the next run.
    await conn.execute(
        "UPDATE speakers SET visibility = 'shared'"
        " WHERE owner_id IS NULL AND visibility <> 'shared'"
    )
    await conn.commit()
    # The name namespace is per-owner, so the old global unique index has to go. Dropped after
    # the columns exist, because the replacement indexes one of them. Safe on a populated
    # table: every pre-migration row has owner_id NULL and names that were globally unique,
    # so none of them can collide under (owner_id, name).
    await conn.execute("DROP INDEX IF EXISTS idx_speakers_name")
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_speakers_owner_name"
        " ON speakers (owner_id, name COLLATE NOCASE)"
    )
    # SQLite treats NULL as distinct in unique indexes, so the composite index above does not
    # constrain two unowned voices (both with owner_id IS NULL) from sharing a name. But the
    # local CLI keeps creating unowned voices (no account to attribute them to), so we need a
    # second partial index to enforce uniqueness among unowned voices while still allowing
    # multiple owners to use the same name.
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_speakers_unowned_name"
        " ON speakers (name COLLATE NOCASE) WHERE owner_id IS NULL"
    )
    await conn.commit()


async def create_tables(conn: aiosqlite.Connection) -> None:
    await conn.executescript(_SCHEMA)
    await conn.commit()
    await migrate(conn)
