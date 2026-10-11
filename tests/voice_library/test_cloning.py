from __future__ import annotations

import asyncio

import numpy as np
import pytest
import pytest_asyncio

from auris.voice_library import cloning, db, speakers


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_get_or_create_speaker_creates_then_reuses(conn):
    first = await cloning.get_or_create_speaker(conn, "Me")
    second = await cloning.get_or_create_speaker(conn, "Me")
    assert first.id == second.id  # reused, not duplicated

    fetched = await speakers.get_speaker_by_name_and_owner(conn, "Me", None)
    assert fetched.id == first.id


@pytest.mark.asyncio
async def test_add_reference_stores_clip_and_row(tmp_path, conn):
    sp = await cloning.get_or_create_speaker(conn, "Me")
    audio = np.zeros(24000, dtype=np.float32)

    index = await cloning.add_reference(conn, tmp_path, sp.id, audio, 24000, "hello passage", 1.0)

    assert index == 1
    assert (tmp_path / sp.id / "clips" / "clip_001.wav").exists()
    clips = await speakers.get_clips(conn, sp.id)
    assert len(clips) == 1
    assert clips[0].transcript == "hello passage"


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_under_race(tmp_path):
    conn = await db.connect(tmp_path / "auris.db")
    try:
        await db.create_tables(conn)
        results = await asyncio.gather(
            cloning.get_or_create_speaker(conn, "Me"),
            cloning.get_or_create_speaker(conn, "me"),
        )
        ids = {sp.id for sp in results}
        assert len(ids) == 1  # same speaker, no duplicate, no IntegrityError surfaced
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_or_create_recovers_from_integrityerror(tmp_path, monkeypatch):
    """Drive the except-IntegrityError branch deterministically: force the initial
    existence check to miss so create_speaker runs against an already-present name,
    raising IntegrityError, after which get_or_create must re-read the winner."""
    conn = await db.connect(tmp_path / "auris.db")
    try:
        await db.create_tables(conn)
        winner = await cloning.speakers.create_speaker(conn, name="Me")  # already exists

        real = cloning.speakers.get_speaker_by_name_and_owner
        calls = {"n": 0}

        async def flaky(c, name, owner_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # pretend we didn't see the existing row -> forces a create
            return await real(c, name, owner_id)

        monkeypatch.setattr(cloning.speakers, "get_speaker_by_name_and_owner", flaky)

        sp = await cloning.get_or_create_speaker(conn, "Me")
        assert sp.id == winner.id  # recovered the existing row, no duplicate
        assert calls["n"] == 2  # first miss, then re-read after IntegrityError
    finally:
        await conn.close()
