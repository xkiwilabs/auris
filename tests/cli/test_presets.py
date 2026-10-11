from __future__ import annotations

import numpy as np
from typer.testing import CliRunner

import auris.cli.commands.tts as tts_cmd
from auris.audio import io
from auris.cli.main import app
from auris.models.base import PresetVoice

runner = CliRunner()


class _RecordingModel:
    name = "kokoro"
    sample_rate = 24000

    def __init__(self):
        self.got = None

    def load(self):
        pass

    def synthesise(self, text, conditioning, speed=1.0):
        self.got = conditioning
        return np.full(24000, 0.1, dtype=np.float32)


def test_tts_with_preset_voice_uses_kokoro(cli_data_dir, monkeypatch):
    captured = {}

    def fake_build(name, settings):
        m = _RecordingModel()
        captured["name"] = name
        captured["model"] = m
        return m

    monkeypatch.setattr(tts_cmd, "build_model", fake_build)
    out = cli_data_dir / "hi.wav"
    result = runner.invoke(app, ["tts", "narrator-uk-f", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert captured["name"] == "kokoro"
    assert isinstance(captured["model"].got, PresetVoice)
    assert captured["model"].got.voice_id == "bf_emma"
    audio, sr = io.load_audio(out)
    assert sr == 24000 and len(audio) == 24000


def test_voices_lists_presets(cli_data_dir):
    result = runner.invoke(app, ["voices"])
    assert result.exit_code == 0, result.output
    assert "narrator-uk-f" in result.output
    assert "preset" in result.output.lower()
