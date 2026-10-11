from __future__ import annotations

import numpy as np
import pytest

from auris.config import load_settings
from auris.models.base import PreparedReference, PresetVoice
from auris.voice_library import cloning, db
from auris.voice_library.resolve import is_preset, preset_voice_rows, resolve_voice


@pytest.mark.asyncio
async def test_resolve_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(":memory:")
    await db.create_tables(conn)
    r = await resolve_voice(conn, settings, "narrator-uk-f")
    assert r.model == "kokoro" and r.kind == "preset"
    assert isinstance(r.conditioning, PresetVoice) and r.conditioning.voice_id == "bf_emma"
    await conn.close()


@pytest.mark.asyncio
async def test_resolve_cloned(tmp_path, monkeypatch):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(":memory:")
    await db.create_tables(conn)
    sp = await cloning.get_or_create_speaker(conn, "Me")
    await cloning.add_reference(
        conn,
        settings.voices_dir,
        sp.id,
        np.zeros(settings.sample_rate, np.float32),
        settings.sample_rate,
        "hi",
        1.0,
    )
    r = await resolve_voice(conn, settings, "Me")
    assert r.model == "f5_tts" and r.kind == "cloned"
    assert isinstance(r.conditioning, PreparedReference)
    await conn.close()


@pytest.mark.asyncio
async def test_resolve_unknown_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    conn = await db.connect(":memory:")
    await db.create_tables(conn)
    with pytest.raises(LookupError):
        await resolve_voice(conn, settings, "ghost")
    await conn.close()


def test_is_preset_and_rows():
    assert is_preset("narrator-uk-f") and not is_preset("Me")
    rows = preset_voice_rows()
    assert {r["name"] for r in rows} >= {"narrator-uk-f"}
    assert all(r["kind"] == "preset" and r["default_model"] == "kokoro" for r in rows)


def test_is_preset_ignores_case_and_surrounding_space():
    assert is_preset("NARRATOR-UK-F") and is_preset("  narrator-uk-f ")
    assert not is_preset("narrator-uk-f-2")
