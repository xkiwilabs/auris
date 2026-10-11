"""Backpressure and the pipeline switches — the two behaviours a deployment depends on."""

from __future__ import annotations

import concurrent.futures

import pytest

from tests.e2e.audio import REFERENCE_TEXT, reference_wav, speech_wav
from tests.e2e.server import instance

pytestmark = pytest.mark.e2e

LONG = " ".join(["The pool has one worker and a queue of one, so this takes a while."] * 3)


def test_concurrent_requests_beyond_the_queue_get_429(admin, server):
    """One worker + a queue of one admits two; the rest are refused rather than piled up.

    Asserted as a floor, not an exact count: the arithmetic bounds what can be admitted, it
    does not schedule when each request arrives, and an exact count would be flaky.
    """

    def synth():
        with server.client() as c:  # a client per thread; httpx.Client is not for sharing
            return c.post(
                "/v1/tts", json={"voice": "narrator-uk-f", "text": LONG, "format": "wav"}
            ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = [f.result() for f in [pool.submit(synth) for _ in range(6)]]

    assert 200 in codes, f"nothing succeeded: {codes}"
    assert 429 in codes, f"nothing was refused — is the queue really 1? {codes}"
    assert set(codes) <= {200, 429}, f"unexpected status: {codes}"

    # And the server is still healthy once the burst clears.
    assert admin.get("/ready").json() == {"ready": True}
    assert (
        admin.post(
            "/v1/tts", json={"voice": "narrator-uk-f", "text": "Still here.", "format": "wav"}
        ).status_code
        == 200
    )


def test_tts_disabled_server_still_transcribes():
    with instance(AURIS_TTS_ENABLED="false", seed=False) as inst, inst.client() as c:
        assert c.get("/v1/info").json()["pipelines"] == {"stt": True, "tts": False}

        r = c.post("/v1/tts", json={"voice": "narrator-uk-f", "text": "hello"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "pipeline_disabled"
        assert "AURIS_TTS_ENABLED" in r.json()["error"]["message"]

        wav = speech_wav("Transcription still works on a synthesis free server.")
        r = c.post(
            "/v1/audio/transcriptions",
            files={"file": ("s.wav", wav.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 200 and r.json()["text"].strip()


def test_stt_disabled_server_clones_only_with_a_transcript():
    with instance(AURIS_STT_ENABLED="false", seed=False) as inst, inst.client() as c:
        assert c.get("/v1/info").json()["pipelines"] == {"stt": False, "tts": True}
        wav, _ = reference_wav()

        r = c.post(
            "/v1/voices/needs-stt/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 503 and r.json()["error"]["code"] == "pipeline_disabled"

        r = c.post(
            "/v1/voices/brings-its-own/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
            data={"ref_text": REFERENCE_TEXT},
        )
        assert r.status_code == 201

        assert (
            c.post(
                "/v1/tts", json={"voice": "brings-its-own", "text": "Cloned without whisper."}
            ).status_code
            == 200
        )
