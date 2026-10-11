from __future__ import annotations

import io as _io
import numpy as np
import soundfile as sf
import pytest

from auris.server.encode import encode_audio


def test_encode_wav_roundtrips():
    audio = np.linspace(-0.5, 0.5, 2400, dtype=np.float32)
    data, ctype = encode_audio(audio, 24000, "wav")
    assert ctype == "audio/wav"
    back, sr = sf.read(_io.BytesIO(data), dtype="float32")
    assert sr == 24000
    assert len(back) == 2400


def test_encode_pcm_is_raw_s16le():
    audio = np.zeros(100, dtype=np.float32)
    data, ctype = encode_audio(audio, 24000, "pcm")
    assert ctype == "audio/pcm"
    assert len(data) == 100 * 2  # int16, no header


def test_encode_rejects_unknown_format():
    with pytest.raises(ValueError):
        encode_audio(np.zeros(10, np.float32), 24000, "flacx")
