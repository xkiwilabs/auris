from __future__ import annotations

import pytest
import aiosqlite

from auris.voice_library import db, speakers


@pytest.mark.asyncio
async def test_create_tables_is_idempotent():
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        await db.create_tables(conn)  # second call must not raise
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = await cur.fetchall()
        names = {r["name"] for r in rows}
        assert {"speakers", "reference_clips"} <= names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_foreign_keys_enabled():
    conn = await db.connect(":memory:")
    try:
        cur = await conn.execute("PRAGMA foreign_keys")
        row = await cur.fetchone()
        assert row["foreign_keys"] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cascade_delete_removes_clips():
    conn = await db.connect(":memory:")
    try:
        await db.create_tables(conn)
        await conn.execute(
            "INSERT INTO speakers (id, name, created_at) "
            "VALUES ('s1', 'Test', '2024-01-01T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO reference_clips (id, speaker_id, filename, transcript, created_at) "
            "VALUES ('r1', 's1', 'clip.wav', 'hello', '2024-01-01T00:00:00Z')"
        )
        await conn.commit()
        await conn.execute("DELETE FROM speakers WHERE id = 's1'")
        await conn.commit()
        cur = await conn.execute("SELECT * FROM reference_clips")
        assert await cur.fetchall() == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_wal_enabled_on_file_db(tmp_path):
    conn = await db.connect(tmp_path / "auris.db")
    try:
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        assert row[0].lower() == "wal"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_duplicate_speaker_name_rejected(tmp_path):
    conn = await db.connect(tmp_path / "auris.db")
    try:
        await db.create_tables(conn)
        await speakers.create_speaker(conn, name="Me")
        with pytest.raises(aiosqlite.IntegrityError):
            await speakers.create_speaker(conn, name="me")  # case-insensitive duplicate
    finally:
        await conn.close()
