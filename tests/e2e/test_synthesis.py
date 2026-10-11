"""Real audio out of every synthesis route, for a preset voice and a cloned one."""

from __future__ import annotations

import time

import numpy as np
import pytest
import soundfile as sf

from tests.e2e.audio import assert_audible
from tests.e2e.server import SEEDED_VOICE

pytestmark = pytest.mark.e2e

TEXT = "The service is running and this sentence was spoken by the model."


@pytest.mark.parametrize("voice", ["narrator-uk-f", SEEDED_VOICE])
def test_tts_returns_audible_audio_for_presets_and_clones(admin, voice, tmp_path):
    r = admin.post("/v1/tts", json={"voice": voice, "text": TEXT, "format": "wav"})
    assert r.status_code == 200, r.text
    out = tmp_path / f"{voice}.wav"
    out.write_bytes(r.content)
    audio = assert_audible(out)
    # A sentence this long is at least a second of speech; the upper bound catches a runaway
    # generation without asserting a wall-clock duration.
    assert 1.0 <= len(audio) / 24000 <= 30.0


def test_openai_speech_returns_decodable_mp3_and_wav(admin, tmp_path):
    from auris.audio import io  # the FFmpeg fallback path, exercised on real output

    mp3 = admin.post(
        "/v1/audio/speech",
        json={"input": TEXT, "voice": "narrator-uk-f", "response_format": "mp3"},
    )
    assert mp3.status_code == 200
    assert mp3.headers["content-type"].startswith("audio/")
    p = tmp_path / "speech.mp3"
    p.write_bytes(mp3.content)
    audio, sr = io.load_audio(p)  # libsndfile cannot read mp3 here => FFmpeg decodes it
    assert sr > 0 and len(audio) > sr  # more than a second

    wav = admin.post(
        "/v1/audio/speech",
        json={"input": TEXT, "voice": "narrator-uk-f", "response_format": "wav"},
    )
    assert wav.status_code == 200
    q = tmp_path / "speech.wav"
    q.write_bytes(wav.content)
    assert_audible(q)


def test_stream_delivers_chunks_before_it_finishes(admin, tmp_path):
    """Streaming's whole point: the first audio arrives well before the last."""
    long_text = " ".join([TEXT] * 4)
    chunks: list[tuple[float, bytes]] = []
    t0 = time.monotonic()
    with admin.stream(
        "POST", "/v1/tts/stream", json={"voice": "narrator-uk-f", "text": long_text}
    ) as r:
        assert r.status_code == 200
        assert r.headers["x-sample-rate"] == "24000"
        for chunk in r.iter_bytes():
            if chunk:
                chunks.append((time.monotonic() - t0, chunk))
    assert len(chunks) > 1, "response was not chunked"
    assert chunks[0][0] < chunks[-1][0], "every chunk arrived at once — this is not streaming"

    # pcm = headerless signed 16-bit little-endian (see auris/server/encode.py).
    raw = b"".join(c for _, c in chunks)
    out = tmp_path / "stream.wav"
    sf.write(out, np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0, 24000)
    assert_audible(out, min_seconds=2.0)


def test_text_over_the_limit_is_rejected(admin):
    limit = admin.get("/v1/info").json()["limits"]["max_input_chars"]
    r = admin.post("/v1/tts", json={"voice": "narrator-uk-f", "text": "a " * limit})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_empty_text_and_unknown_voice_are_rejected(admin):
    empty = admin.post("/v1/tts", json={"voice": "narrator-uk-f", "text": "   "})
    assert empty.status_code == 422

    unknown = admin.post("/v1/tts", json={"voice": "no-such-voice", "text": "hello"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "voice_not_found"
