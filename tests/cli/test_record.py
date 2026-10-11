from __future__ import annotations

import asyncio

import numpy as np
from typer.testing import CliRunner

from auris.audio.transcript import Transcript

import auris.cli.commands.record as record_cmd
from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import db, speakers

runner = CliRunner()


def _fake_record(seconds, sample_rate, on_tick=None):
    return np.zeros(int(seconds * sample_rate), dtype=np.float32)


def _fake_transcribe(audio, sample_rate, **kwargs):
    return Transcript(text="this is what I actually said")


def _patch_io(monkeypatch):
    monkeypatch.setattr(record_cmd, "record_audio", _fake_record)
    monkeypatch.setattr(record_cmd, "transcribe_reference", _fake_transcribe)


def _fetch(name):
    async def _run():
        conn = await db.connect(load_settings().db_path)
        try:
            sp = await speakers.get_speaker_by_name_and_owner(conn, name, None)
            clips = await speakers.get_clips(conn, sp.id) if sp else []
            return sp, clips
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_record_creates_speaker_and_stores_transcription(cli_data_dir, monkeypatch):
    _patch_io(monkeypatch)
    result = runner.invoke(app, ["record", "Me", "--seconds", "10"])
    assert result.exit_code == 0, result.output

    sp, clips = _fetch("Me")
    assert sp is not None  # speaker auto-created
    assert len(clips) == 1
    # transcript comes from ASR of the recording, NOT the printed passage
    assert clips[0].transcript == "this is what I actually said"


def test_record_appends_to_existing_speaker(cli_data_dir, monkeypatch):
    _patch_io(monkeypatch)
    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0

    assert runner.invoke(app, ["record", "Me", "--seconds", "10"]).exit_code == 0
    sp, clips = _fetch("Me")
    assert len(clips) == 1

    assert runner.invoke(app, ["record", "Me", "--seconds", "10"]).exit_code == 0
    sp2, clips2 = _fetch("Me")
    assert sp2.id == sp.id
    assert len(clips2) == 2


def test_record_default_length_leaves_room_past_the_cap(cli_data_dir, monkeypatch):
    captured = {}

    def _capture(seconds, sample_rate, on_tick=None):
        captured["seconds"] = seconds
        return np.zeros(int(seconds * sample_rate), dtype=np.float32)

    monkeypatch.setattr(record_cmd, "record_audio", _capture)
    monkeypatch.setattr(record_cmd, "transcribe_reference", _fake_transcribe)
    result = runner.invoke(app, ["record", "Me"])  # no --seconds → default
    assert result.exit_code == 0, result.output
    # More than the 10 s the clone keeps: a recording that stops on the timer is usually mid-word,
    # and the spare time lets the cut fall in a pause before the cap instead.
    assert captured["seconds"] == 12.0


def test_record_rejects_over_10s(cli_data_dir):
    # The mic recording is capped at the f5-TTS reference limit.
    assert runner.invoke(app, ["record", "Me", "--seconds", "12"]).exit_code != 0


def test_record_free_mode_hides_passage(cli_data_dir, monkeypatch):
    _patch_io(monkeypatch)
    result = runner.invoke(app, ["record", "Me", "--free", "--seconds", "10"])
    assert result.exit_code == 0, result.output
    assert record_cmd.RAINBOW_PASSAGE not in result.output
    assert "free-form" in result.output.lower()
    _, clips = _fetch("Me")
    assert clips[0].transcript == "this is what I actually said"  # still transcribed + stored


def test_record_custom_passage_is_shown(cli_data_dir, monkeypatch):
    _patch_io(monkeypatch)
    result = runner.invoke(
        app, ["record", "Me", "--passage", "My custom sentence to read.", "--seconds", "10"]
    )
    assert result.exit_code == 0, result.output
    assert "My custom sentence to read." in result.output


def test_record_no_speech_detected_errors(cli_data_dir, monkeypatch):
    monkeypatch.setattr(record_cmd, "record_audio", _fake_record)
    monkeypatch.setattr(
        record_cmd, "transcribe_reference", lambda audio, sr, **kw: Transcript(text="   ")
    )
    result = runner.invoke(app, ["record", "Me", "--seconds", "10"])
    assert result.exit_code == 1
    assert "no speech" in result.output.lower()


def test_record_reports_missing_audio_dependency(cli_data_dir, monkeypatch):
    def _raise(seconds, sample_rate, on_tick=None):
        raise RuntimeError("recording needs the 'audio' extra (sounddevice + PortAudio)")

    monkeypatch.setattr(record_cmd, "record_audio", _raise)
    result = runner.invoke(app, ["record", "Me", "--seconds", "10"])
    assert result.exit_code == 1
    assert "audio" in result.output


def test_record_reports_missing_transcribe_dependency(cli_data_dir, monkeypatch):
    monkeypatch.setattr(record_cmd, "record_audio", _fake_record)

    def _raise(audio, sample_rate, **kwargs):
        raise RuntimeError("transcription needs faster-whisper (the 'model' extra)")

    monkeypatch.setattr(record_cmd, "transcribe_reference", _raise)
    result = runner.invoke(app, ["record", "Me", "--seconds", "10"])
    assert result.exit_code == 1
    assert "faster-whisper" in result.output


def test_record_passes_asr_model_to_transcription(cli_data_dir, monkeypatch):
    seen = {}

    def _capture(audio, sample_rate, model=None, **kwargs):
        seen["model"] = model
        return Transcript(text="this is what I actually said")

    monkeypatch.setattr(record_cmd, "record_audio", _fake_record)
    monkeypatch.setattr(record_cmd, "transcribe_reference", _capture)
    result = runner.invoke(app, ["record", "Me", "--seconds", "10", "--asr-model", "small.en"])
    assert result.exit_code == 0, result.output
    assert seen["model"] == "small.en"
