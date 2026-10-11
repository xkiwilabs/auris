from __future__ import annotations

import asyncio

import numpy as np
from typer.testing import CliRunner

import auris.cli.commands.record as record_cmd
import auris.cli.commands.say as say_cmd
from auris.cli.main import app
from auris.config import load_settings
from auris.models.base import TTSModel
from auris.voice_library import cloning, db, speakers, users

runner = CliRunner()


def _fake_record(seconds, sample_rate, on_tick=None):
    return np.zeros(int(seconds * sample_rate), dtype=np.float32)


class _FakeModel(TTSModel):
    name = "f5_tts"
    sample_rate = 24000

    def load(self):
        pass

    def synthesise(self, text, reference, speed=1.0):
        return np.full(100, 0.1, dtype=np.float32)


def _seed_speaker(monkeypatch):
    monkeypatch.setattr(record_cmd, "record_audio", _fake_record)
    monkeypatch.setattr(record_cmd, "transcribe_audio", lambda audio, sr, **kw: "seed transcript")
    assert runner.invoke(app, ["record", "Me", "--seconds", "10"]).exit_code == 0


def test_say_streams_plays_and_saves(cli_data_dir, monkeypatch):
    _seed_speaker(monkeypatch)
    monkeypatch.setattr(say_cmd, "build_model", lambda name, settings: _FakeModel())

    played = []
    monkeypatch.setattr(say_cmd, "play_stream", lambda chunks, sr: played.extend(chunks))

    out = cli_data_dir / "say.wav"
    result = runner.invoke(
        app,
        ["say", "Me", "--text", "First sentence here. Second sentence here.", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert len(played) == 2  # two sentences → two streamed chunks
    assert out.exists()  # --out saved the concatenation


def test_say_unknown_speaker_errors(cli_data_dir):
    result = runner.invoke(app, ["say", "Ghost", "--text", "A real sentence here."])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_say_rejects_both_text_and_file(cli_data_dir, monkeypatch):
    _seed_speaker(monkeypatch)
    script = cli_data_dir / "script.txt"
    script.write_text("hello there world.")
    both = runner.invoke(app, ["say", "Me", "--text", "hi there world.", "--file", str(script)])
    assert both.exit_code != 0


def test_say_with_no_text_uses_sample(cli_data_dir, monkeypatch):
    # Neither --text nor --file → speak a short sample to test the voice.
    _seed_speaker(monkeypatch)
    monkeypatch.setattr(say_cmd, "build_model", lambda name, settings: _FakeModel())
    spoken = []
    monkeypatch.setattr(say_cmd, "play_stream", lambda chunks, sr: spoken.extend(chunks))
    result = runner.invoke(app, ["say", "Me"])
    assert result.exit_code == 0, result.output
    assert len(spoken) >= 1  # the sample sentence was synthesised


def test_say_reports_unknown_model(cli_data_dir, monkeypatch):
    _seed_speaker(monkeypatch)

    def _raise(name, settings):
        raise ValueError("unknown model: bogus")

    monkeypatch.setattr(say_cmd, "build_model", _raise)
    result = runner.invoke(app, ["say", "Me", "--text", "A real sentence here."])
    assert result.exit_code == 1
    assert "unknown model" in result.output.lower()


def test_say_reports_synthesis_failure(cli_data_dir, monkeypatch):
    _seed_speaker(monkeypatch)

    class _BadModel(TTSModel):
        name = "f5_tts"
        sample_rate = 24000

        def load(self):
            pass

        def synthesise(self, text, reference, speed=1.0):
            raise FileNotFoundError("weights missing")

    monkeypatch.setattr(say_cmd, "build_model", lambda name, settings: _BadModel())
    # fake play_stream that simply drains the generator so the model error surfaces
    monkeypatch.setattr(say_cmd, "play_stream", lambda chunks, sr: list(chunks))
    result = runner.invoke(app, ["say", "Me", "--text", "A real sentence here."])
    assert result.exit_code == 1
    assert "synthesis failed" in result.output.lower()


# --- fix round 2: `say` gets the same unowned-first precedence and --owner as `tts` ----------


def _with_db(fn):
    async def go():
        conn = await db.connect(load_settings().db_path)
        try:
            await db.create_tables(conn)
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


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


def _fake_stream_synthesis(seen):
    def _stream(model, text, reference, speed=1.0):
        seen.append(reference.transcript)
        yield np.full(100, 0.1, dtype=np.float32)

    return _stream


def test_say_resolves_unowned_voice_over_a_colleagues_namesake(cli_data_dir, monkeypatch):
    settings = load_settings()
    seen: list[str] = []

    async def seed(conn):
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")
        await _seed_with_clip(conn, settings, "narrator", None, "operator's own line")

    _with_db(seed)
    monkeypatch.setattr(say_cmd, "build_model", lambda name, settings: _FakeModel())
    monkeypatch.setattr(say_cmd, "stream_synthesis", _fake_stream_synthesis(seen))
    monkeypatch.setattr(say_cmd, "play_stream", lambda chunks, sr: list(chunks))

    result = runner.invoke(app, ["say", "narrator", "--text", "A real sentence here."])
    assert result.exit_code == 0, result.output
    assert seen == ["operator's own line"]


def test_say_owner_option_selects_that_accounts_voice(cli_data_dir, monkeypatch):
    settings = load_settings()
    seen: list[str] = []

    async def seed(conn):
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")
        await _seed_with_clip(conn, settings, "narrator", None, "operator's own line")

    _with_db(seed)
    monkeypatch.setattr(say_cmd, "build_model", lambda name, settings: _FakeModel())
    monkeypatch.setattr(say_cmd, "stream_synthesis", _fake_stream_synthesis(seen))
    monkeypatch.setattr(say_cmd, "play_stream", lambda chunks, sr: list(chunks))

    result = runner.invoke(
        app,
        ["say", "narrator", "--text", "A real sentence here.", "--owner", "bob@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert seen == ["bob's line"]
