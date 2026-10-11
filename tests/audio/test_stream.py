from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from auris.audio import stream
from auris.audio.stream import stream_synthesis
from auris.models.base import PreparedReference, TTSModel
from auris.text.chunker import split_into_chunks


class _FakeModel(TTSModel):
    name = "f5_tts"
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def load(self):
        pass

    def synthesise(self, text, reference, speed=1.0):
        self.calls.append(text)
        # a marker array whose length encodes which chunk this is
        return np.full(len(self.calls), 0.1, dtype=np.float32)


def _ref():
    return PreparedReference(
        audio=np.zeros(10, dtype=np.float32), sample_rate=24000, transcript="t"
    )


def test_yields_one_chunk_per_text_chunk_in_order():
    model = _FakeModel()
    text = "First sentence here. Second sentence here. Third sentence here."
    out = list(stream_synthesis(model, text, _ref()))

    assert model.calls == split_into_chunks(text)  # generated in chunk order
    assert len(out) == len(model.calls)
    assert [len(a) for a in out] == [1, 2, 3]  # order preserved


def test_empty_text_yields_nothing():
    model = _FakeModel()
    assert list(stream_synthesis(model, "   ", _ref())) == []
    assert model.calls == []


def test_worker_exception_propagates_to_consumer():
    class _Boom(TTSModel):
        name = "x"
        sample_rate = 24000

        def load(self):
            pass

        def synthesise(self, text, reference, speed=1.0):
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        list(stream_synthesis(_Boom(), "A real sentence here.", _ref()))


def test_early_consumer_exit_does_not_hang():
    model = _FakeModel()
    gen = stream_synthesis(model, "One here. Two here. Three here. Four here.", _ref())
    first = next(gen)  # consume only the first chunk
    gen.close()  # abandon the rest — finally must cancel + drain + join without hanging
    assert first is not None


class _SlowModel:
    name = "fake"
    sample_rate = 24000

    def __init__(self):
        self.calls = 0

    def load(self):
        pass

    def synthesise(self, text, reference, speed=1.0):
        self.calls += 1
        time.sleep(0.05)
        return np.zeros(10, dtype=np.float32)


def test_early_close_stops_worker_promptly():
    model = _SlowModel()
    ref = PreparedReference(np.zeros(10, np.float32), 24000, "x")
    gen = stream.stream_synthesis(model, "One. Two. Three. Four. Five.", ref)
    next(gen)  # consume first chunk only
    gen.close()  # abandon early — must not run all remaining chunks
    time.sleep(0.2)
    assert model.calls <= 3  # stopped soon after cancel, not all 5
    assert threading.active_count() < 50  # worker thread joined, not leaked
