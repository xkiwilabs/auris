from __future__ import annotations

import pytest

from auris.audio.transcript import Segment, TranscribeOptions, Transcript, Word


def test_transcript_defaults_are_empty():
    t = Transcript(text="", segments=(), language="en", language_probability=0.0, duration_s=0.0)
    assert t.text == "" and t.segments == ()


def test_segment_holds_words():
    w = Word(start=0.0, end=0.5, word="hi", probability=0.9)
    s = Segment(id=0, start=0.0, end=0.5, text="hi", words=(w,), no_speech_prob=0.01)
    assert s.words[0].word == "hi"


def test_options_default_to_server_friendly_values():
    o = TranscribeOptions()
    assert o.model is None  # => AURIS_ASR_MODEL
    assert o.language is None  # => auto-detect
    assert o.task == "transcribe"
    assert o.word_timestamps is False
    assert o.vad_filter is True
    assert o.initial_prompt is None


def test_options_reject_an_unknown_task():
    with pytest.raises(ValueError, match="task must be"):
        TranscribeOptions(task="summarise").validated()


def test_options_accept_translate():
    assert TranscribeOptions(task="translate").validated().task == "translate"


def test_options_are_frozen():
    o = TranscribeOptions()
    with pytest.raises(Exception):
        o.task = "translate"
