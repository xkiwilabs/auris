from __future__ import annotations

import numpy as np
import pytest

from auris.config import load_settings
from auris.config import Settings
from auris.voice_library import cloning, db, references, speakers, users
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.references import load_speaker_reference


async def _seed_speaker(settings, name="Me", transcript="hello passage"):
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        sp = await cloning.get_or_create_speaker(conn, name)
        audio = np.zeros(24000, dtype=np.float32)
        await cloning.add_reference(conn, settings.voices_dir, sp.id, audio, 24000, transcript, 1.0)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_returns_reference_and_model(tmp_path):
    settings = Settings(data_dir=tmp_path)
    await _seed_speaker(settings)

    result = await load_speaker_reference(settings, "Me")

    assert result.reference.transcript == "hello passage"
    assert len(result.reference.audio) == 24000
    assert result.default_model == "f5_tts"


@pytest.mark.asyncio
async def test_unknown_speaker_raises(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(LookupError, match="not found"):
        await load_speaker_reference(settings, "Ghost")


@pytest.mark.asyncio
async def test_speaker_without_clip_raises(tmp_path):
    settings = Settings(data_dir=tmp_path)
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        await cloning.get_or_create_speaker(conn, "Me")
    finally:
        await conn.close()

    with pytest.raises(LookupError, match="clone"):
        await load_speaker_reference(settings, "Me")


@pytest.mark.asyncio
async def test_load_reference_with_shared_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    await db.create_tables(conn)
    sp = await cloning.get_or_create_speaker(conn, "Me")
    audio = np.zeros(settings.sample_rate, dtype=np.float32)
    await cloning.add_reference(
        conn, settings.voices_dir, sp.id, audio, settings.sample_rate, "hi", 1.0
    )

    ref = await references.load_reference(conn, settings, "Me")
    assert ref.reference.transcript == "hi"
    assert ref.default_model == "f5_tts"
    with pytest.raises(LookupError):
        await references.load_reference(conn, settings, "Ghost")
    await conn.close()


async def _seed_with_clip(conn, settings, name, owner_id, transcript):
    sp = await speakers.create_speaker(conn, name, owner_id=owner_id)
    await cloning.add_reference(
        conn,
        settings.voices_dir,
        sp.id,
        np.zeros(settings.sample_rate, dtype=np.float32),
        settings.sample_rate,
        transcript,
        1.0,
    )
    return sp


@pytest.mark.asyncio
async def test_load_reference_prefers_the_unowned_namesake(tmp_path, monkeypatch):
    """The local operator has no account, so an unowned voice is treated as the operator's own
    namespace and wins over a colleague's same-named voice — exactly as `clone.py` resolves
    `auris clone`'s target, so the two verbs agree on which voice a bare name means."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")
        await _seed_with_clip(conn, settings, "narrator", None, "operator's own line")

        ref = await references.load_reference(conn, settings, "narrator")
        assert ref.reference.transcript == "operator's own line"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_load_reference_resolves_a_single_owned_namesake(tmp_path, monkeypatch):
    """With no unowned voice by this name, exactly one owned match is still unambiguous."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

        ref = await references.load_reference(conn, settings, "narrator")
        assert ref.reference.transcript == "bob's line"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_load_reference_refuses_to_silently_pick_between_two_owners(tmp_path, monkeypatch):
    """Two owners, no unowned row: silently picking whichever SQLite returns first is exactly
    the determinism defect this round exists to close."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", alice.id, "alice's line")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

        with pytest.raises(AmbiguousVoice) as exc:
            await references.load_reference(conn, settings, "narrator")
        assert "--owner" in str(exc.value)
        assert set(exc.value.owners) == {alice.id, bob.id}
    finally:
        await conn.close()
