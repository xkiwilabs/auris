from __future__ import annotations

import io as _io
import numpy as np
import soundfile as sf

from auris.voice_library import cloning
from tests.server.conftest import run_on_app_loop


def _add_voice(client):
    settings = client.app.state.settings
    conn = client.app.state.db

    async def seed():
        sp = await cloning.get_or_create_speaker(conn, "Me")
        audio = np.zeros(settings.sample_rate, dtype=np.float32)
        await cloning.add_reference(
            conn, settings.voices_dir, sp.id, audio, settings.sample_rate, "hello world", 1.0
        )

    run_on_app_loop(client, seed())


def test_tts_requires_auth(client):
    r = client.post("/v1/tts", json={"text": "hi", "voice": "Me"}, headers={"Authorization": ""})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_tts_full_returns_wav(client):
    _add_voice(client)
    r = client.post("/v1/tts", json={"text": "hi", "voice": "Me", "format": "wav"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/wav")
    audio, sr = sf.read(_io.BytesIO(r.content), dtype="float32")
    assert sr == 24000
    assert len(audio) > 0


def test_tts_full_defaults_to_mp3(client):
    # No format given => settings.default_format (mp3), so remote clients get a compact
    # payload over the network by default rather than raw WAV.
    _add_voice(client)
    r = client.post("/v1/tts", json={"text": "hi", "voice": "Me"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert len(r.content) > 0


def test_tts_preset_voice_routes_to_kokoro(client):
    # A preset name needs no cloned speaker — it resolves to the kokoro pool (FakeModel here).
    r = client.post("/v1/tts", json={"text": "hi", "voice": "narrator-uk-f", "format": "wav"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/wav")


def test_tts_unknown_voice_404(client):
    r = client.post("/v1/tts", json={"text": "hi", "voice": "Ghost"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "voice_not_found"


def test_tts_over_length_422(client):
    _add_voice(client)
    big = "a " * 2000
    r = client.post("/v1/tts", json={"text": big, "voice": "Me"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_tts_stream_returns_pcm_chunks(client):
    _add_voice(client)
    with client.stream("POST", "/v1/tts/stream", json={"text": "One. Two.", "voice": "Me"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/pcm")
        body = b"".join(r.iter_bytes())
    assert len(body) > 0 and len(body) % 2 == 0  # s16le
