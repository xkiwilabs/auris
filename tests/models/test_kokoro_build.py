from __future__ import annotations

import numpy as np
import pytest

from auris.models import build_model_on_device
from auris.models.base import PreparedReference


def test_build_kokoro_does_not_load_weights():
    m = build_model_on_device("kokoro", "cpu")
    assert m.name == "kokoro"
    assert m.sample_rate == 24000  # matches auris internal rate, no resampling


def test_kokoro_rejects_clone_conditioning():
    m = build_model_on_device("kokoro", "cpu")
    with pytest.raises(TypeError):
        m.synthesise("hi", PreparedReference(np.zeros(8, np.float32), 24000, "x"))
