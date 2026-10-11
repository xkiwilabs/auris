from __future__ import annotations

import numpy as np
import pytest

from auris.models.base import Conditioning, PreparedReference, PresetVoice


def test_presetvoice_fields():
    pv = PresetVoice("af_heart", "a")
    assert pv.voice_id == "af_heart" and pv.lang_code == "a"


def test_conditioning_union_admits_both():
    a: Conditioning = PreparedReference(np.zeros(8, np.float32), 24000, "hi")
    b: Conditioning = PresetVoice("af_heart", "a")
    assert isinstance(a, PreparedReference) and isinstance(b, PresetVoice)


def test_f5_rejects_preset_conditioning():
    # The type-guard must fire before any torch import, so an unloaded model is fine.
    from auris.models.f5_tts import F5TTSModel

    with pytest.raises(TypeError):
        F5TTSModel(device="cpu").synthesise("hello", PresetVoice("af_heart", "a"))
