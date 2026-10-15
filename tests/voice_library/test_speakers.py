from __future__ import annotations

import pytest
import pytest_asyncio

from auris.voice_library import db, speakers, cloning
from auris.voice_library.exceptions import NameTaken


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_create_and_get_speaker(conn):
    created = await speakers.create_speaker(conn, name="Me")
    assert created.id
    assert created.name == "Me"
    assert created.default_model == "f5_tts"

    fetched = await speakers.get_speaker_by_name_and_owner(conn, "Me", None)
    assert fetched is not None
    assert fetched.id == created.id

    fetched_ci = await speakers.get_speaker_by_name_and_owner(conn, "me", None)
    assert fetched_ci is not None
    assert fetched_ci.id == created.id


@pytest.mark.asyncio
async def test_get_missing_speaker_returns_none(conn):
    assert await speakers.get_speaker_by_name_and_owner(conn, "Nobody", None) is None


@pytest.mark.asyncio
async def test_add_and_list_clips(conn):
    sp = await speakers.create_speaker(conn, name="Me")
    clip = await speakers.add_clip(
        conn,
        speaker_id=sp.id,
        filename="clip_001.wav",
        transcript="hello there",
        duration_s=1.5,
    )
    assert clip.transcript == "hello there"

    clips = await speakers.get_clips(conn, sp.id)
    assert len(clips) == 1
    assert clips[0].filename == "clip_001.wav"
    assert clip.id
    assert clips[0].id == clip.id
    assert clips[0].speaker_id == sp.id


@pytest.mark.asyncio
async def test_get_clips_unknown_speaker_returns_empty(conn):
    assert await speakers.get_clips(conn, "no-such-id") == []


@pytest.mark.asyncio
async def test_stats_and_delete(tmp_path):
    conn = await db.connect(tmp_path / "auris.db")
    await db.create_tables(conn)
    sp = await cloning.get_or_create_speaker(conn, "Me")
    await speakers.add_clip(conn, sp.id, "clip_001.wav", "hi", 2.0)
    await speakers.add_clip(conn, sp.id, "clip_002.wav", "yo", 3.0)

    stats = await speakers.list_visible_with_stats(conn, None)
    # Unowned, so 'shared' — see speakers.UNOWNED_IS_ALWAYS_SHARED.
    assert stats == [("Me", 2, 5.0, "f5_tts", None, "shared", None)]

    await speakers.delete_speaker(conn, sp.id)
    assert await speakers.get_speaker_by_name_and_owner(conn, "Me", None) is None
    assert await speakers.get_clips(conn, sp.id) == []  # FK cascade
    await conn.close()


@pytest.mark.asyncio
async def test_rename_speaker_and_collision(conn):
    a = await speakers.create_speaker(conn, "Alice")
    await speakers.create_speaker(conn, "Bob")
    renamed = await speakers.rename_speaker(conn, a.id, "Alicia")
    assert renamed.name == "Alicia"
    assert await speakers.get_speaker_by_name_and_owner(conn, "alicia", None) is not None
    with pytest.raises(NameTaken):
        await speakers.rename_speaker(conn, a.id, "bob")  # case-insensitive
    # the aborted UPDATE must not leave an open transaction holding the write lock
    third = await speakers.create_speaker(conn, "Carol")
    assert third.name == "Carol"


@pytest.mark.asyncio
async def test_get_clip_by_filename_is_scoped_to_its_speaker(conn):
    """The route's index → row lookup (`DELETE /v1/voices/{name}/clips/{index}`). Clip
    filenames restart at `clip_001.wav` for every voice, so the speaker_id in the WHERE is what
    stops one voice's index reaching another's row. (This test used to drive the orphaned
    `delete_clip`; `delete_clip_if_not_last` below covers the deletion, so what is left worth
    pinning here is the lookup.)"""
    sp = await speakers.create_speaker(conn, "Alice")
    c1 = await speakers.add_clip(conn, sp.id, "clip_001.wav", "one", 4.0)
    await speakers.add_clip(conn, sp.id, "clip_002.wav", "two", 5.0)
    other = await speakers.create_speaker(conn, "Bob")
    await speakers.add_clip(conn, other.id, "clip_001.wav", "theirs", 4.0)

    assert (await speakers.get_clip_by_filename(conn, sp.id, "clip_001.wav")).id == c1.id
    assert (await speakers.get_clip_by_filename(conn, other.id, "clip_001.wav")).id != c1.id
    assert await speakers.get_clip_by_filename(conn, sp.id, "clip_009.wav") is None


@pytest.mark.asyncio
async def test_delete_clip_if_not_last_keeps_the_final_clip(conn):
    sp = await speakers.create_speaker(conn, "Alice")
    c1 = await speakers.add_clip(conn, sp.id, "clip_001.wav", "one", 4.0)
    c2 = await speakers.add_clip(conn, sp.id, "clip_002.wav", "two", 5.0)
    assert await speakers.delete_clip_if_not_last(conn, sp.id, c1.id) is True
    assert await speakers.delete_clip_if_not_last(conn, sp.id, c2.id) is False
    assert [c.id for c in await speakers.get_clips(conn, sp.id)] == [c2.id]
    # a clip id belonging to another speaker is never deleted through this speaker
    other = await speakers.create_speaker(conn, "Bob")
    assert await speakers.delete_clip_if_not_last(conn, other.id, c2.id) is False


@pytest.mark.asyncio
async def test_validate_name_rejects_path_and_control_characters(conn):
    for bad in ("", "  ", "a/b", "a\\b", "a\tb", "x" * 65):
        with pytest.raises(ValueError):
            await speakers.create_speaker(conn, bad)
    assert (await speakers.create_speaker(conn, "  Mike R.  ")).name == "Mike R."
    # people's names: accents, apostrophes and ampersands are fine
    assert (
        await speakers.create_speaker(conn, "Zoë O'Brien & Müller")
    ).name == "Zoë O'Brien & Müller"
