from __future__ import annotations

import pytest

from auris.audio.transcript import Segment, Transcript, Word
from auris.server import transcript_format as fmt


def _transcript():
    return Transcript(
        text="Hello world. Second line.",
        segments=(
            Segment(
                id=0,
                start=0.0,
                end=1.5,
                text="Hello world.",
                words=(Word(0.0, 0.5, "Hello", 0.9),),
                no_speech_prob=0.01,
            ),
            Segment(id=1, start=1.5, end=3725.25, text="Second line."),
        ),
        language="en",
        language_probability=0.99,
        duration_s=3725.25,
    )


def test_srt_uses_comma_milliseconds_and_1_based_indices():
    out = fmt.to_srt(_transcript())
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello world.\n")
    assert "2\n00:00:01,500 --> 01:02:05,250\nSecond line." in out


def test_vtt_has_a_webvtt_header_and_dot_milliseconds():
    out = fmt.to_vtt(_transcript())
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.500" in out
    assert "01:02:05.250" in out  # over an hour formats correctly


def test_verbose_json_shape():
    body = fmt.to_verbose_json(_transcript(), word_timestamps=False, task="transcribe")
    assert body["task"] == "transcribe"
    assert body["language"] == "en"
    assert body["duration"] == 3725.25
    assert body["text"] == "Hello world. Second line."
    assert body["segments"][0]["start"] == 0.0
    assert "words" not in body  # only when asked for


def test_verbose_json_echoes_the_requested_task():
    body = fmt.to_verbose_json(_transcript(), word_timestamps=False, task="translate")
    assert body["task"] == "translate"


def test_render_passes_the_task_into_verbose_json():
    body, _ = fmt.render(_transcript(), "verbose_json", word_timestamps=False, task="translate")
    assert body["task"] == "translate"


def test_verbose_json_includes_words_when_requested():
    body = fmt.to_verbose_json(_transcript(), word_timestamps=True, task="transcribe")
    assert body["words"][0] == {"word": "Hello", "start": 0.0, "end": 0.5}


def test_render_json_is_the_default_shape():
    body, ctype = fmt.render(_transcript(), "json", word_timestamps=False)
    assert body == {"text": "Hello world. Second line."}
    assert ctype == "application/json"


def test_render_text_returns_plain_text():
    body, ctype = fmt.render(_transcript(), "text", word_timestamps=False)
    assert body == "Hello world. Second line."
    assert ctype == "text/plain; charset=utf-8"


def test_render_rejects_an_unknown_format():
    with pytest.raises(ValueError, match="response_format must be one of"):
        fmt.render(_transcript(), "pdf", word_timestamps=False)
