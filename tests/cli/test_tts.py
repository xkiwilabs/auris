from __future__ import annotations

import asyncio

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

import auris.cli.commands.tts as tts_cmd
from auris.audio import io
from auris.cli.main import app
from auris.config import load_settings
from auris.models.base import PreparedReference
from auris.voice_library import cloning, db, speakers, users

runner = CliRunner()


class _FakeModel:
    name = "f5_tts"
    sample_rate = 24000

    def load(self):
        pass

    def synthesise(self, text, reference: PreparedReference, speed=1.0):
        assert reference.transcript == "hello world"
        return np.full(24000, 0.1, dtype=np.float32)


def _write_ref(path, seconds=4, sr=22050):
    sf.write(str(path), np.zeros(seconds * sr, dtype=np.float32), sr)


def test_tts_generates_output_wav(cli_data_dir, monkeypatch):
    # force the CLI to use the fake model instead of real f5-TTS
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _FakeModel())

    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    runner.invoke(app, ["speakers", "add", "Me"])
    runner.invoke(app, ["clone", "Me", "--ref", str(ref), "--ref-text", "hello world"])

    out = cli_data_dir / "hi.wav"
    result = runner.invoke(app, ["tts", "Me", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    audio, sr = io.load_audio(out)
    assert sr == 24000
    assert len(audio) == 24000


def test_tts_without_clip_errors(cli_data_dir, monkeypatch):
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _FakeModel())
    runner.invoke(app, ["speakers", "add", "Me"])
    result = runner.invoke(app, ["tts", "Me", "--text", "hi", "--out", str(cli_data_dir / "x.wav")])
    assert result.exit_code != 0
    assert "clone" in result.output.lower()


def test_tts_unknown_speaker_errors(cli_data_dir, monkeypatch):
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _FakeModel())
    result = runner.invoke(
        app, ["tts", "Ghost", "--text", "hi", "--out", str(cli_data_dir / "x.wav")]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_tts_missing_clip_file_errors(cli_data_dir, monkeypatch):
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _FakeModel())
    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    assert runner.invoke(app, ["speakers", "add", "Me"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["clone", "Me", "--ref", str(ref), "--ref-text", "hello world"]
        ).exit_code
        == 0
    )
    # remove the stored clip so tts cannot read it back
    for f in (cli_data_dir / "voices").rglob("*.wav"):
        f.unlink()
    result = runner.invoke(
        app, ["tts", "Me", "--text", "hi", "--out", str(cli_data_dir / "out.wav")]
    )
    assert result.exit_code == 1
    assert result.output  # clean error message, not an unhandled traceback


# --- fix round 2: resolution must agree with `clone.py`'s unowned-first precedence -----------


class _CapturingModel:
    name = "f5_tts"
    sample_rate = 24000

    def __init__(self, sink):
        self._sink = sink

    def load(self):
        pass

    def synthesise(self, text, reference: PreparedReference, speed=1.0):
        self._sink.append(reference.transcript)
        return np.full(24000, 0.1, dtype=np.float32)


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


def test_tts_resolves_unowned_voice_over_a_colleagues_namesake(cli_data_dir, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel(seen))
    settings = load_settings()

    async def seed(conn):
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")
        await _seed_with_clip(conn, settings, "narrator", None, "operator's own line")

    _with_db(seed)
    out = cli_data_dir / "out.wav"
    result = runner.invoke(app, ["tts", "narrator", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert seen == ["operator's own line"]


def test_tts_resolves_the_single_owned_namesake_when_none_is_unowned(cli_data_dir, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel(seen))
    settings = load_settings()

    async def seed(conn):
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

    _with_db(seed)
    out = cli_data_dir / "out.wav"
    result = runner.invoke(app, ["tts", "narrator", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert seen == ["bob's line"]


def test_tts_ambiguous_name_exits_with_an_owner_hint(cli_data_dir, monkeypatch):
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel([]))
    settings = load_settings()

    async def seed(conn):
        alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", alice.id, "alice's line")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

    _with_db(seed)
    out = cli_data_dir / "out.wav"
    result = runner.invoke(app, ["tts", "narrator", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 1
    assert "--owner" in result.output
    # The emails, not just a count: the message asks for an address, so it has to supply the
    # choices. `auris voices share` already named them; this path gave "2 voices are named
    # 'narrator'" and left the operator to find the owners themselves. Over HTTP the count-only
    # wording stays, deliberately — see resolve.describe_ambiguity.
    assert "alice@example.com" in result.output
    assert "bob@example.com" in result.output


def test_tts_owner_option_selects_that_accounts_voice(cli_data_dir, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel(seen))
    settings = load_settings()

    async def seed(conn):
        alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", alice.id, "alice's line")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

    _with_db(seed)
    out = cli_data_dir / "out.wav"
    result = runner.invoke(
        app, ["tts", "narrator", "--text", "hi", "--out", str(out), "--owner", "bob@example.com"]
    )
    assert result.exit_code == 0, result.output
    assert seen == ["bob's line"]


def test_tts_owner_option_with_unknown_account_hints_users_add(cli_data_dir, monkeypatch):
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel([]))
    settings = load_settings()
    _with_db(lambda c: _seed_with_clip(c, settings, "narrator", None, "line"))
    out = cli_data_dir / "out.wav"
    result = runner.invoke(
        app,
        ["tts", "narrator", "--text", "hi", "--out", str(out), "--owner", "nobody@example.com"],
    )
    assert result.exit_code == 1
    assert "auris users add" in result.output


def test_clone_and_tts_agree_on_which_voice_a_bare_name_means(cli_data_dir, monkeypatch):
    """The consistency property this round exists to establish: `auris clone` (which targets
    the operator's own unowned namespace) and `auris tts` must pick the *same* voice for the
    same bare name on the same box, even with a colleague's same-named voice present."""
    seen: list[str] = []
    monkeypatch.setattr(tts_cmd, "build_model", lambda name, settings: _CapturingModel(seen))
    settings = load_settings()

    async def seed(conn):
        bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
        await _seed_with_clip(conn, settings, "narrator", bob.id, "bob's line")

    _with_db(seed)
    assert runner.invoke(app, ["speakers", "add", "narrator"]).exit_code == 0  # unowned

    ref = cli_data_dir / "ref.wav"
    _write_ref(ref)
    clone_result = runner.invoke(
        app, ["clone", "narrator", "--ref", str(ref), "--ref-text", "the unowned line"]
    )
    assert clone_result.exit_code == 0, clone_result.output

    out = cli_data_dir / "out.wav"
    tts_result = runner.invoke(app, ["tts", "narrator", "--text", "hi", "--out", str(out)])
    assert tts_result.exit_code == 0, tts_result.output
    assert seen == ["the unowned line"]  # tts picked up exactly what clone just wrote
