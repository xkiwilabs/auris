from __future__ import annotations

import numpy as np
import pytest

from auris.models.base import PreparedReference, TTSModel


def test_prepared_reference_holds_audio_and_transcript():
    ref = PreparedReference(
        audio=np.zeros(10, dtype=np.float32), sample_rate=24000, transcript="hi"
    )
    assert ref.sample_rate == 24000
    assert ref.transcript == "hi"
    assert ref.audio.dtype == np.float32
    assert ref.audio.ndim == 1


def test_ttsmodel_is_abstract():
    with pytest.raises(TypeError):
        TTSModel()  # cannot instantiate abstract base


def test_build_model_on_device_passes_device():
    from auris.models import build_model_on_device

    model = build_model_on_device("f5_tts", "cuda:1")
    assert model.name == "f5_tts"
    assert model._device_pref == "cuda:1"  # device threaded through, no torch import/load
