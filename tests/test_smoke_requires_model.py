from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from auris.audio import io
from auris.cli.main import app

runner = CliRunner()

# Drop a real speech clip here + its transcript to use a human voice as the reference.
# Anchored to this file so it resolves regardless of pytest's working directory.
SAMPLE = Path(__file__).parent / "assets" / "ref.wav"
SAMPLE_TEXT = "this is a short reference recording for the auris smoke test"


def _reference() -> tuple[Path, str]:
    """The reference clip to clone from, and its transcript.

    A real human recording is the better conditioning, so `tests/assets/ref.wav` wins whenever
    someone has dropped one in. Falling back to synthesised speech matters because this test
    used to *skip* when that file was absent — which it always was, so the CLI's real
    clone → tts path went unexercised entirely.
    """
    if SAMPLE.exists():
        return SAMPLE, SAMPLE_TEXT
    from tests.e2e.audio import REFERENCE_TEXT, speech_wav  # Kokoro; already a requires_model dep

    return speech_wav(REFERENCE_TEXT), REFERENCE_TEXT


@pytest.mark.requires_model
def test_real_clone_and_tts(tmp_path, monkeypatch):
    sample, sample_text = _reference()
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))

    assert runner.invoke(app, ["speakers", "add", "Smoke"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["clone", "Smoke", "--ref", str(sample), "--ref-text", sample_text]
        ).exit_code
        == 0
    )

    out = tmp_path / "out.wav"
    result = runner.invoke(app, ["tts", "Smoke", "--text", "Hello from auris.", "--out", str(out)])
    assert result.exit_code == 0, result.output
    audio, sr = io.load_audio(out)
    assert sr == 24000
    assert len(audio) > sr // 2  # at least ~0.5s of audio
