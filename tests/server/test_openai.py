from __future__ import annotations

from tests.server.test_synthesis import _add_voice


def test_speech_full_pcm(client):
    _add_voice(client)
    r = client.post(
        "/v1/audio/speech",
        json={
            "model": "f5_tts",
            "input": "hi",
            "voice": "Me",
            "response_format": "pcm",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/pcm")
    assert len(r.content) % 2 == 0


def test_speech_streamed(client):
    _add_voice(client)
    with client.stream(
        "POST",
        "/v1/audio/speech",
        json={
            "model": "f5_tts",
            "input": "One. Two.",
            "voice": "Me",
            "response_format": "pcm",
            "stream_format": "audio",
        },
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert len(body) > 0


def test_speech_full_defaults_to_mp3(client):
    # OpenAI's /v1/audio/speech defaults to mp3; we match that for compatibility.
    _add_voice(client)
    r = client.post("/v1/audio/speech", json={"input": "hi", "voice": "Me"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert len(r.content) > 0


def test_speech_unknown_voice_404(client):
    r = client.post("/v1/audio/speech", json={"model": "f5_tts", "input": "hi", "voice": "Ghost"})
    assert r.status_code == 404
