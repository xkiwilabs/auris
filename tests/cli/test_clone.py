from __future__ import annotations

import asyncio

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from auris.audio.transcript import Transcript

import auris.cli.commands.clone as clone_cmd
from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import db, speakers, users

runner = CliRunner()


def _write_ref(path, seconds=4, sr=22050):
    # sr intentionally off the 24 kHz internal rate to exercise the resample path
    sf.write(str(path), np.zeros(seconds * sr, dtype=np.float32), sr)


def _get_clips(name):
    async def _run():
        conn = await db.connect(load_settings().db_path)
        try:
            sp = await speakers.get_speaker_by_name_and_owner(conn, name, None)
            return await speakers.get_clips(conn, sp.id)
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_clone_stores_clip_and_transcript(cli_data_dir):
    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)

    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    result = runner.invoke(app, ["clone", "Me", "--ref", str(ref), "--ref-text", "hello world"])
    assert result.exit_code == 0, result.output

    clips = list((cli_data_dir / "voices").rglob("clip_001.wav"))
    assert len(clips) == 1

    # the transcript was persisted (f5-TTS conditions on it)
    db_clips = _get_clips("Me")
    assert len(db_clips) == 1
    assert db_clips[0].transcript == "hello world"


def test_clone_auto_transcribes_when_ref_text_omitted(cli_data_dir, monkeypatch):
    monkeypatch.setattr(
        clone_cmd,
        "transcribe_reference",
        lambda audio, sr, **kw: Transcript(text="auto transcribed text"),
    )
    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)

    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    result = runner.invoke(app, ["clone", "Me", "--ref", str(ref)])  # no --ref-text
    assert result.exit_code == 0, result.output

    db_clips = _get_clips("Me")
    assert len(db_clips) == 1
    assert db_clips[0].transcript == "auto transcribed text"


def test_clone_rejects_short_clip(cli_data_dir):
    ref = cli_data_dir / "short.wav"
    _write_ref(ref, seconds=1)  # < 3s
    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    result = runner.invoke(app, ["clone", "Me", "--ref", str(ref), "--ref-text", "hi"])
    assert result.exit_code == 1
    assert "3 seconds" in result.output or "too short" in result.output.lower()


def test_clone_unknown_speaker_errors(cli_data_dir):
    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    result = runner.invoke(app, ["clone", "Ghost", "--ref", str(ref), "--ref-text", "hi"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_clone_rejects_unreadable_audio(cli_data_dir):
    ref = cli_data_dir / "bad.wav"
    ref.write_bytes(b"this is not a wav file")
    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    result = runner.invoke(app, ["clone", "Me", "--ref", str(ref), "--ref-text", "hi"])
    assert result.exit_code == 1
    assert result.output  # a clean error message, not a silent crash


def test_clone_passes_asr_model_to_transcription(cli_data_dir, monkeypatch):
    seen = {}

    def _capture(audio, sample_rate, model=None, **kwargs):
        seen["model"] = model
        return Transcript(text="auto transcribed text")

    monkeypatch.setattr(clone_cmd, "transcribe_reference", _capture)
    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    result = runner.invoke(app, ["clone", "Me", "--ref", str(ref), "--asr-model", "medium.en"])
    assert result.exit_code == 0, result.output
    assert seen["model"] == "medium.en"


def test_clone_does_not_reach_another_owners_voice(cli_data_dir):
    """A per-owner namespace means a name match belonging to someone else must not be found by
    the operator's own (unowned) `clone` — the old global-by-name lookup would otherwise append
    the operator's clip to another account's private voice."""

    async def seed():
        conn = await db.connect(load_settings().db_path)
        try:
            await db.create_tables(conn)
            u = await users.create_user(conn, "owner@example.com", "a-good-long-password")
            sp = await speakers.create_speaker(conn, "n", owner_id=u.id)
            await speakers.add_clip(conn, sp.id, "clip_001.wav", "existing", 4.0)
            return sp
        finally:
            await conn.close()

    owned = asyncio.run(seed())

    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    result = runner.invoke(app, ["clone", "n", "--ref", str(ref), "--ref-text", "hi"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()

    async def read_clips():
        conn = await db.connect(load_settings().db_path)
        try:
            return await speakers.get_clips(conn, owned.id)
        finally:
            await conn.close()

    clips = asyncio.run(read_clips())
    assert len(clips) == 1  # the operator's clip was not appended to it
