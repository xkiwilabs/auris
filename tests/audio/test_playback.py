from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from auris.audio.playback import play_audio


def test_play_audio_sends_to_device(monkeypatch):
    calls = {}

    def fake_play(data, samplerate):
        calls.update(length=len(data), samplerate=samplerate, dtype=data.dtype)

    fake_sd = types.SimpleNamespace(play=fake_play, wait=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    play_audio(np.zeros(2400, dtype=np.float32), 24000)

    assert calls["length"] == 2400
    assert calls["samplerate"] == 24000
    assert calls["dtype"] == np.float32


def test_play_audio_missing_dependency_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(RuntimeError, match="audio"):
        play_audio(np.zeros(10, dtype=np.float32), 24000)


def test_play_stream_writes_chunks_in_order_and_closes(monkeypatch):
    import sys
    import types

    written = []
    closed = {"value": False}

    class _FakeStream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def write(self, data):
            written.append(len(data))

        def stop(self):
            pass

        def close(self):
            closed["value"] = True

    fake_sd = types.SimpleNamespace(OutputStream=_FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    from auris.audio.playback import play_stream

    play_stream([np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)], 24000)

    assert written == [100, 200]
    assert closed["value"] is True


def test_play_stream_missing_dependency_raises(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    from auris.audio.playback import play_stream

    with pytest.raises(RuntimeError, match="audio"):
        play_stream([np.zeros(10, dtype=np.float32)], 24000)
