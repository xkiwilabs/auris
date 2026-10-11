from __future__ import annotations

import numpy as np
import pytest

from auris.audio.transcribe import transcribe_detailed
from auris.audio.transcript import TranscribeOptions


@pytest.mark.requires_model
def test_real_transcription_returns_a_structured_transcript():
    """Opt-in: needs the model extra and downloads tiny.en on first run.

    Run with: pytest -m requires_model tests/audio/test_transcribe_requires_model.py

    No audio fixture is committed: three seconds of silence is generated here, which is
    enough to prove the real faster-whisper path loads, decodes, and fills a Transcript.
    """
    audio = np.zeros(3 * 16000, dtype=np.float32)
    result = transcribe_detailed(audio, 16000, TranscribeOptions(model="tiny.en"))
    assert isinstance(result.text, str)
    assert result.duration_s > 0
    assert result.language
