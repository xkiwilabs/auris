from __future__ import annotations

import numpy as np
import pytest

from auris.models import build_model_on_device
from auris.models.base import PresetVoice

# Real Kokoro inference. Needs the `kokoro` extra + the espeak-ng system package; excluded
# from the default suite. Run on the box with:
#   .venv/bin/python -m pytest -m requires_model tests/models/test_kokoro_requires_model.py


@pytest.mark.requires_model
@pytest.mark.parametrize("voice_id,lang", [("af_heart", "a"), ("bf_emma", "b")], ids=["us", "uk"])
def test_kokoro_synthesises_each_lang(voice_id, lang):
    model = build_model_on_device("kokoro", "cpu")
    model.load()
    audio = model.synthesise("This is a Kokoro preset voice.", PresetVoice(voice_id, lang))
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.ndim == 1 and len(audio) > 0
