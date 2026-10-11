from __future__ import annotations

import numpy as np

from auris.models.base import PreparedReference
from auris.models.f5_tts import F5TTSModel


class _FakeInner:
    """Stands in for f5_tts.api.F5TTS — records the infer() call."""

    def __init__(self):
        self.calls = []

    def infer(self, ref_file, ref_text, gen_text, **kwargs):
        self.calls.append(
            {
                "ref_file": ref_file,
                "ref_text": ref_text,
                "gen_text": gen_text,
                "speed": kwargs.get("speed"),
            }
        )
        # return 1 second of silence at 24kHz, like the real API: (wav, sr, spec)
        return np.zeros(24000, dtype=np.float32), 24000, None


def _model_with_fake():
    m = F5TTSModel(device="cpu")
    m._model = _FakeInner()  # bypass real load()
    return m


def test_synthesise_calls_infer_with_transcript_and_text(tmp_path):
    m = _model_with_fake()
    ref = PreparedReference(
        audio=np.zeros(24000, dtype=np.float32),
        sample_rate=24000,
        transcript="this is the reference text",
    )
    out = m.synthesise("hello world", ref, speed=1.0)

    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32
    call = m._model.calls[0]
    assert call["ref_text"] == "this is the reference text"
    assert call["gen_text"] == "hello world"
    assert call["speed"] == 1.0


def test_synthesise_resamples_to_model_rate():
    m = _model_with_fake()

    # make the fake return 48kHz so the wrapper must downsample to 24kHz
    def infer_48k(ref_file, ref_text, gen_text, **kwargs):
        return np.zeros(48000, dtype=np.float32), 48000, None

    m._model.infer = infer_48k
    ref = PreparedReference(
        audio=np.zeros(24000, dtype=np.float32), sample_rate=24000, transcript="x"
    )
    out = m.synthesise("hi", ref)
    assert abs(len(out) - 24000) <= 1  # 48k -> 24k


def test_load_is_idempotent():
    m = _model_with_fake()
    original = m._model
    m.load()  # already loaded; must be a no-op, not re-import or reset
    assert m._model is original
