from __future__ import annotations

import io as _io

import numpy as np
import soundfile as sf

from auris.audio.asr_models import DEFAULT_MODEL
from auris.server.routers import openai

from .conftest import FakeAsrPool, run_on_app_loop


def _wav_bytes(seconds=2, sr=16000):
    buf = _io.BytesIO()
    sf.write(buf, np.zeros(seconds * sr, dtype=np.float32), sr, format="WAV")
    return buf.getvalue()


def _pool(client):
    return client.app.state.asr_pool


def test_transcription_uses_the_server_default_when_no_model_is_sent(client):
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
    )
    assert r.status_code == 200, r.text
    assert _pool(client).jobs[-1].options.model == DEFAULT_MODEL


def test_transcription_accepts_the_openai_whisper_1_alias(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"model": "whisper-1"},
    )
    assert r.status_code == 200, r.text
    assert _pool(client).jobs[-1].options.model == DEFAULT_MODEL


def test_remote_caller_can_choose_an_allowed_model(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"model": "small.en"},
    )
    assert r.status_code == 200, r.text
    assert _pool(client).jobs[-1].options.model == "small.en"


def test_disallowed_model_is_rejected_before_any_work(client):
    before = len(_pool(client).jobs)
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"model": "gpt-4o-transcribe"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_request"
    assert len(_pool(client).jobs) == before  # never reached the pool


def test_audio_models_lists_the_allowlist(client):
    r = client.get("/v1/audio/models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default"] == DEFAULT_MODEL
    ids = [m["id"] for m in body["models"]]
    assert "small.en" in ids and "large-v3-turbo" in ids
    assert all(m["desc"] for m in body["models"])


def test_audio_models_needs_auth(client):
    r = client.get("/v1/audio/models", headers={"Authorization": ""})
    assert r.status_code == 401


def test_clone_upload_honours_asr_model(client):
    r = client.post(
        "/v1/voices/Remote/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
        data={"asr_model": "distil-small.en"},
    )
    assert r.status_code == 201, r.text
    assert _pool(client).jobs[-1].options.model == "distil-small.en"


def test_clone_upload_rejects_a_disallowed_asr_model(client):
    r = client.post(
        "/v1/voices/Remote/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
        data={"asr_model": "nope"},
    )
    assert r.status_code == 422, r.text


def test_transcription_defaults_to_json(client):
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "hello world"}


def test_transcription_verbose_json_has_segments(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": "verbose_json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "en"
    assert body["segments"][0]["text"] == "hello world"


def test_transcription_srt_is_plain_text(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": "srt"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert "00:00:00,000 --> 00:00:01,000" in r.text


def test_transcription_vtt_is_plain_text(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": "vtt"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.000" in r.text


def test_transcription_text_format_is_the_bare_transcript(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": "text"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "hello world"


def test_transcription_options_reach_the_pool(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={
            "model": "small.en",
            "language": "fr",
            "prompt": "auris",
            "timestamp_granularities[]": "word",
            "response_format": "verbose_json",
        },
    )
    assert r.status_code == 200, r.text
    options = _pool(client).jobs[-1].options
    assert options.model == "small.en"
    assert options.language == "fr"
    assert options.initial_prompt == "auris"
    assert options.word_timestamps is True


def test_segment_granularity_does_not_ask_for_word_timestamps(client):
    client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"timestamp_granularities[]": "segment"},
    )
    assert _pool(client).jobs[-1].options.word_timestamps is False


def test_transcription_defaults_come_from_settings(client):
    client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")})
    options = _pool(client).jobs[-1].options
    assert options.language is None  # AURIS_ASR_LANGUAGE is empty => detect
    assert options.task == "transcribe"
    assert options.vad_filter is True
    assert options.initial_prompt is None
    assert options.word_timestamps is False


def test_language_auto_means_detect(client):
    client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"language": "auto"},
    )
    assert _pool(client).jobs[-1].options.language is None


def test_translate_task_is_accepted(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"task": "translate"},
    )
    assert r.status_code == 200, r.text
    assert _pool(client).jobs[-1].options.task == "translate"


def test_verbose_json_reports_the_requested_task(client):
    for task in ("transcribe", "translate"):
        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
            data={"task": task, "response_format": "verbose_json"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["task"] == task


def test_unknown_response_format_is_422(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": "pdf"},
    )
    assert r.status_code == 422, r.text


def test_unknown_task_is_422(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", _wav_bytes(), "audio/wav")},
        data={"task": "summarise"},
    )
    assert r.status_code == 422, r.text


def test_empty_upload_is_422(client):
    before = len(_pool(client).jobs)
    r = client.post("/v1/audio/transcriptions", files={"file": ("clip.wav", b"", "audio/wav")})
    assert r.status_code == 422, r.text
    assert len(_pool(client).jobs) == before


def test_oversize_upload_is_422(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, "asr_max_upload_mb", 0)
    before = len(_pool(client).jobs)
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
    )
    assert r.status_code == 422, r.text
    assert "asr_max_upload_mb" in r.json()["error"]["message"]
    assert len(_pool(client).jobs) == before


def test_oversize_upload_is_rejected_before_the_body_is_read(client, monkeypatch):
    # read() would materialise the whole body in memory, so the reported size is checked
    # first; a read here means the cap is being enforced too late.
    from starlette.datastructures import UploadFile

    async def _boom(self, size=-1):
        raise AssertionError("the request body must not be read past the size cap")

    monkeypatch.setattr(UploadFile, "read", _boom)
    monkeypatch.setattr(client.app.state.settings, "asr_max_upload_mb", 0)
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
    )
    assert r.status_code == 422, r.text
    assert "asr_max_upload_mb" in r.json()["error"]["message"]


def test_undecodable_upload_is_400(client):
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("clip.wav", b"not audio", "audio/wav")}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "bad_audio"
    assert r.json()["error"]["message"] == "could not decode uploaded audio"


def test_clone_upload_transcribes_through_the_pool(client):
    r = client.post(
        "/v1/voices/Pooled/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["transcript"] == "hello world"
    # cloning must never auto-detect
    assert _pool(client).jobs[-1].options.language == "en"
    assert _pool(client).jobs[-1].options.vad_filter is False


def test_clone_upload_with_ref_text_skips_the_pool(client):
    before = len(_pool(client).jobs)
    r = client.post(
        "/v1/voices/Given/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
        data={"ref_text": "a supplied transcript"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["transcript"] == "a supplied transcript"
    assert len(_pool(client).jobs) == before


def test_clone_upload_is_503_when_stt_is_disabled(client):
    # Sharing one admission budget means AURIS_STT_ENABLED=false also turns off the
    # transcription half of cloning; ref_text is then the only way in.
    client.app.state.asr_pool = None
    r = client.post(
        "/v1/voices/NoStt/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
    )
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "pipeline_disabled"


def test_clone_upload_with_ref_text_works_when_stt_is_disabled(client):
    # ...but a clone that supplies its own transcript needs no decode, so a TTS-only
    # server must still accept it.
    client.app.state.asr_pool = None
    r = client.post(
        "/v1/voices/NoStt/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=4), "audio/wav")},
        data={"ref_text": "a supplied transcript"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["transcript"] == "a supplied transcript"


def test_saturated_gate_returns_429_before_decoding(client, monkeypatch):
    """The 429 has to land *before* the upload is decoded — a decoded float32 array is the
    largest thing a request holds, so admitting it and rejecting afterwards is what the
    gate exists to prevent."""
    pool = FakeAsrPool(queue_maxsize=0)  # one admission slot in total
    client.app.state.asr_pool = pool

    def _never(*args, **kwargs):
        raise AssertionError("decoded an upload that the gate should have rejected")

    monkeypatch.setattr(openai, "_decode_upload", _never)

    held = pool.admit()
    run_on_app_loop(client, held.__aenter__())  # the one slot is taken
    try:
        r = client.post(
            "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
        )
    finally:
        run_on_app_loop(client, held.__aexit__(None, None, None))

    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "overloaded"
    assert pool.jobs == []


def test_gate_is_released_so_the_next_caller_gets_through(client):
    # One admission slot in total, so a served request that failed to hand its slot back
    # would make the second call 429.
    client.app.state.asr_pool = FakeAsrPool(queue_maxsize=0)
    for _ in range(3):
        r = client.post(
            "/v1/audio/transcriptions", files={"file": ("clip.wav", _wav_bytes(), "audio/wav")}
        )
        assert r.status_code == 200, r.text


def test_audio_longer_than_asr_max_seconds_is_422(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, "asr_max_seconds", 1.0)
    before = len(_pool(client).jobs)
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("long.wav", _wav_bytes(seconds=3), "audio/wav")},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_request"
    assert "asr_max_seconds" in r.json()["error"]["message"]
    assert len(_pool(client).jobs) == before  # never reached the pool


def test_audio_within_asr_max_seconds_is_accepted(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, "asr_max_seconds", 10.0)
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("ok.wav", _wav_bytes(seconds=3), "audio/wav")},
    )
    assert r.status_code == 200, r.text
