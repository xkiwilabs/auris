from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from auris.audio.record import record_audio


def test_record_audio_returns_mono_float32(monkeypatch):
    calls = {}

    def fake_rec(frames, samplerate, channels, dtype):
        calls.update(frames=frames, samplerate=samplerate, channels=channels, dtype=dtype)
        return np.zeros((frames, 1), dtype=np.float32)

    fake_sd = types.SimpleNamespace(rec=fake_rec, wait=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    out = record_audio(2, 24000)
    assert out.dtype == np.float32
    assert out.ndim == 1
    assert len(out) == 48000
    assert calls["samplerate"] == 24000
    assert calls["channels"] == 1


def test_record_audio_missing_dependency_raises(monkeypatch):
    # Force `import sounddevice` to fail, simulating a machine without it / PortAudio.
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(RuntimeError, match="sounddevice"):
        record_audio(2, 24000)


def test_record_audio_calls_on_tick_each_second(monkeypatch):
    import time as time_module

    fake_sd = types.SimpleNamespace(
        rec=lambda frames, samplerate, channels, dtype: np.zeros((frames, 1), dtype=np.float32),
        wait=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(time_module, "sleep", lambda s: None)  # don't actually wait

    ticks = []
    record_audio(3, 24000, on_tick=ticks.append)
    assert ticks == [3, 2, 1]
