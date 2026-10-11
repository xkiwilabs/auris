from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from auris.audio import transcribe
from auris.audio.transcribe import transcribe_audio


def _fake_whisper(segment_texts):
    class _Seg:
        def __init__(self, i, text):
            self.id, self.text = i, text
            self.start, self.end = float(i), float(i) + 1.0
            self.words, self.no_speech_prob = [], 0.0

    class _Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, language=None, **kwargs):
            return ([_Seg(i, t) for i, t in enumerate(segment_texts)], {})

    return types.SimpleNamespace(WhisperModel=_Model)


def test_transcribe_joins_and_strips_segments(monkeypatch):
    transcribe._reset_model_cache()
    monkeypatch.setitem(sys.modules, "faster_whisper", _fake_whisper(["  hello ", " world "]))
    assert transcribe_audio(np.zeros(24000, dtype=np.float32), 24000) == "hello world"


def test_transcribe_returns_empty_when_no_speech(monkeypatch):
    transcribe._reset_model_cache()
    monkeypatch.setitem(sys.modules, "faster_whisper", _fake_whisper([]))
    assert transcribe_audio(np.zeros(24000, dtype=np.float32), 24000) == ""


def test_transcribe_missing_dependency_raises(monkeypatch):
    transcribe._reset_model_cache()
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match="faster-whisper"):
        transcribe_audio(np.zeros(10, dtype=np.float32), 24000)


def test_whisper_model_is_loaded_once(monkeypatch):
    transcribe._reset_model_cache()  # test hook
    loads = {"n": 0}

    class _FakeWhisper:
        def __init__(self, *a, **k):
            loads["n"] += 1

        def transcribe(self, audio, language="en", **kwargs):
            seg = types.SimpleNamespace(id=0, start=0.0, end=1.0, text="hello", words=[])
            return [seg], None

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = _FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    audio = np.zeros(16000, dtype=np.float32)
    transcribe.transcribe_audio(audio, 16000)
    transcribe.transcribe_audio(audio, 16000)
    assert loads["n"] == 1  # cached, not reloaded per call


def test_auto_device_prefers_gpu_when_one_is_visible(monkeypatch):
    monkeypatch.setattr(transcribe, "_cuda_available", lambda: True)
    assert transcribe._resolve_runtime("auto", "auto") == ("cuda", 0, "float16")


def test_auto_device_falls_back_to_cpu_int8(monkeypatch):
    monkeypatch.setattr(transcribe, "_cuda_available", lambda: False)
    assert transcribe._resolve_runtime("auto", "auto") == ("cpu", 0, "int8")


def test_cuda_index_becomes_a_separate_device_index():
    # ctranslate2 does not understand the "cuda:N" string the TTS models use.
    assert transcribe._resolve_runtime("cuda:1", "auto") == ("cuda", 1, "float16")


def test_mps_falls_back_to_cpu():
    # ctranslate2 has no Metal backend, so mps would fail at load time.
    assert transcribe._resolve_runtime("mps", "auto") == ("cpu", 0, "int8")


def test_explicit_compute_type_is_honoured():
    assert transcribe._resolve_runtime("cpu", "float32") == ("cpu", 0, "float32")


def test_settings_drive_the_whisper_constructor(monkeypatch):
    transcribe._reset_model_cache()
    monkeypatch.setenv("AURIS_ASR_MODEL", "small.en")
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cuda:1")
    monkeypatch.setenv("AURIS_ASR_COMPUTE_TYPE", "int8_float16")
    seen = {}

    class _Model:
        def __init__(self, model, device=None, device_index=None, compute_type=None):
            seen.update(
                model=model, device=device, device_index=device_index, compute_type=compute_type
            )

        def transcribe(self, audio, language=None, **kwargs):
            return ([], {})

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_Model))
    transcribe_audio(np.zeros(16000, dtype=np.float32), 16000)
    assert seen == {
        "model": "small.en",
        "device": "cuda",
        "device_index": 1,
        "compute_type": "int8_float16",
    }


def test_different_models_get_their_own_cached_instance(monkeypatch):
    transcribe._reset_model_cache()
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cpu")
    loads = []

    class _Model:
        def __init__(self, model, **kwargs):
            loads.append(model)

        def transcribe(self, audio, language=None, **kwargs):
            return ([], {})

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_Model))
    audio = np.zeros(16000, dtype=np.float32)
    transcribe_audio(audio, 16000, model="base.en")
    transcribe_audio(audio, 16000, model="small.en")
    transcribe_audio(audio, 16000, model="base.en")  # cached, not reloaded
    assert loads == ["base.en", "small.en"]


def _fake_whisper_module(segments, info):
    """A faster_whisper stand-in whose WhisperModel returns the given segments/info."""

    class _Model:
        last_kwargs: dict = {}

        def __init__(self, model, device=None, device_index=None, compute_type=None):
            self.model = model

        def transcribe(self, audio, **kwargs):
            _Model.last_kwargs = kwargs
            return iter(segments), info

    return types.SimpleNamespace(WhisperModel=_Model), _Model


def _seg(id, start, end, text, words=(), no_speech_prob=0.0):
    return types.SimpleNamespace(
        id=id, start=start, end=end, text=text, words=words, no_speech_prob=no_speech_prob
    )


def _info(language="en", probability=0.99, duration=1.5):
    return types.SimpleNamespace(
        language=language, language_probability=probability, duration=duration
    )


def test_run_whisper_maps_segments_into_a_transcript(monkeypatch):
    from auris.audio.transcript import TranscribeOptions

    segments = [_seg(0, 0.0, 0.5, "  Hello "), _seg(1, 0.5, 1.5, " world. ")]
    mod, model_cls = _fake_whisper_module(segments, _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()

    whisper = transcribe.load_whisper("base.en", "cpu", "int8")
    result = transcribe.run_whisper(
        whisper, np.zeros(16000, dtype=np.float32), 16000, TranscribeOptions()
    )

    assert result.text == "Hello world."
    assert [s.text for s in result.segments] == ["Hello", "world."]
    assert result.segments[1].start == 0.5
    assert result.language == "en"
    assert result.language_probability == 0.99
    assert result.duration_s == 1.5


def test_run_whisper_maps_word_timestamps(monkeypatch):
    from auris.audio.transcript import TranscribeOptions

    word = types.SimpleNamespace(start=0.0, end=0.4, word="Hello", probability=0.8)
    mod, _ = _fake_whisper_module([_seg(0, 0.0, 0.4, "Hello", words=[word])], _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()

    whisper = transcribe.load_whisper("base.en", "cpu", "int8")
    result = transcribe.run_whisper(
        whisper,
        np.zeros(16000, dtype=np.float32),
        16000,
        TranscribeOptions(word_timestamps=True),
    )
    assert result.segments[0].words[0].word == "Hello"
    assert result.segments[0].words[0].probability == 0.8


def test_run_whisper_passes_options_through(monkeypatch):
    from auris.audio.transcript import TranscribeOptions

    mod, model_cls = _fake_whisper_module([], _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()

    whisper = transcribe.load_whisper("base.en", "cpu", "int8")
    transcribe.run_whisper(
        whisper,
        np.zeros(16000, dtype=np.float32),
        16000,
        TranscribeOptions(
            language="fr",
            task="translate",
            word_timestamps=True,
            vad_filter=False,
            initial_prompt="auris, f5-TTS",
        ),
    )
    kw = model_cls.last_kwargs
    assert kw["language"] == "fr"
    assert kw["task"] == "translate"
    assert kw["word_timestamps"] is True
    assert kw["vad_filter"] is False
    assert kw["initial_prompt"] == "auris, f5-TTS"


def test_auto_detect_sends_language_none(monkeypatch):
    from auris.audio.transcript import TranscribeOptions

    mod, model_cls = _fake_whisper_module([], _info(language="de"))
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()

    whisper = transcribe.load_whisper("base.en", "cpu", "int8")
    result = transcribe.run_whisper(
        whisper, np.zeros(16000, dtype=np.float32), 16000, TranscribeOptions(language=None)
    )
    assert model_cls.last_kwargs["language"] is None
    assert result.language == "de"  # reported back from the model


def test_transcribe_audio_still_returns_a_string_and_forces_english(monkeypatch):
    mod, model_cls = _fake_whisper_module([_seg(0, 0.0, 1.0, " hi ")], _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cpu")
    transcribe._reset_model_cache()

    out = transcribe.transcribe_audio(np.zeros(16000, dtype=np.float32), 16000)
    assert out == "hi"
    # cloning must never auto-detect: a mis-detected reference degrades f5 conditioning
    assert model_cls.last_kwargs["language"] == "en"
    # ...nor run VAD: prepare_reference already trimmed the silence, and dropping a leading
    # word here would desynchronise the stored audio from the transcript f5 conditions on.
    assert model_cls.last_kwargs["vad_filter"] is False


def test_load_whisper_is_not_cached(monkeypatch):
    mod, _ = _fake_whisper_module([], _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()
    a = transcribe.load_whisper("base.en", "cpu", "int8")
    b = transcribe.load_whisper("base.en", "cpu", "int8")
    assert a is not b  # the pool needs N distinct workers


def test_get_whisper_is_cached(monkeypatch):
    mod, _ = _fake_whisper_module([], _info())
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe._reset_model_cache()
    assert transcribe.get_whisper("base.en", "cpu", "int8") is transcribe.get_whisper(
        "base.en", "cpu", "int8"
    )


def test_transcribe_detailed_prefers_the_option_model_over_settings(monkeypatch):
    from auris.audio.transcript import TranscribeOptions

    transcribe._reset_model_cache()
    monkeypatch.setenv("AURIS_ASR_MODEL", "small.en")
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cpu")
    loads = []

    class _Model:
        def __init__(self, model, **kwargs):
            loads.append(model)

        def transcribe(self, audio, **kwargs):
            return ([], _info())

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_Model))
    audio = np.zeros(16000, dtype=np.float32)
    transcribe.transcribe_detailed(audio, 16000, TranscribeOptions(model="base.en"))
    transcribe.transcribe_detailed(audio, 16000, TranscribeOptions())  # falls back to Settings
    assert loads == ["base.en", "small.en"]


def test_transcribe_detailed_auto_detects_when_given_no_options(monkeypatch):
    mod, model_cls = _fake_whisper_module([_seg(0, 0.0, 1.0, " bonjour ")], _info(language="fr"))
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cpu")
    transcribe._reset_model_cache()

    result = transcribe.transcribe_detailed(np.zeros(16000, dtype=np.float32), 16000)

    # only the cloning path forces English; the STT surfaces auto-detect
    assert model_cls.last_kwargs["language"] is None
    assert result.text == "bonjour"
    assert result.language == "fr"


def test_get_whisper_builds_a_cold_model_once_under_concurrency(monkeypatch):
    """`_pick` runs on real pool worker threads, so two requests naming the same cold,
    non-resident model can race the cache. Without the lock both build a WhisperModel —
    a wasted load and a wasted chunk of VRAM, last write wins."""
    import threading
    import time

    transcribe._reset_model_cache()
    built: list[str] = []

    def _slow_load(model, device, compute_type):
        built.append(model)
        time.sleep(0.05)  # wide enough for an unsynchronised second caller to slip in
        return object()

    monkeypatch.setattr(transcribe, "load_whisper", _slow_load)

    start = threading.Barrier(2)
    got: list[object] = []

    def _race():
        start.wait(5)
        got.append(transcribe.get_whisper("small.en", "cpu", "int8"))

    threads = [threading.Thread(target=_race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert built == ["small.en"]  # built exactly once
    assert got[0] is got[1]  # ...and both callers got that one instance
    transcribe._reset_model_cache()
