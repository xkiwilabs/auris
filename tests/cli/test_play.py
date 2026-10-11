from __future__ import annotations

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

import auris.cli.commands.play as play_cmd
from auris.cli.main import app

runner = CliRunner()


def test_play_loads_and_plays_file(tmp_path, monkeypatch):
    played = {}

    def fake_play(audio, sample_rate):
        played.update(length=len(audio), sr=sample_rate)

    monkeypatch.setattr(play_cmd, "play_audio", fake_play)

    wav = tmp_path / "x.wav"
    sf.write(str(wav), np.zeros(24000, dtype=np.float32), 24000)

    result = runner.invoke(app, ["play", str(wav)])
    assert result.exit_code == 0, result.output
    assert played["length"] == 24000
    assert played["sr"] == 24000


def test_play_missing_dependency_errors(tmp_path, monkeypatch):
    def _raise(audio, sample_rate):
        raise RuntimeError("playback needs the 'audio' extra (sounddevice + PortAudio)")

    monkeypatch.setattr(play_cmd, "play_audio", _raise)
    wav = tmp_path / "x.wav"
    sf.write(str(wav), np.zeros(100, dtype=np.float32), 24000)

    result = runner.invoke(app, ["play", str(wav)])
    assert result.exit_code == 1
    assert "audio" in result.output
