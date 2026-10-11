from __future__ import annotations

import io as _io
import numpy as np
import soundfile as sf
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.requires_model


def _real_wav(seconds=5, sr=24000):
    # a non-silent reference so f5-TTS produces usable output
    t = np.linspace(0, seconds, seconds * sr, dtype=np.float32)
    audio = 0.1 * np.sin(2 * np.pi * 120 * t)
    buf = _io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    return buf.getvalue()


def test_clone_then_tts_end_to_end(tmp_path, monkeypatch):
    """Runs the REAL pool (build_pool → f5-TTS) on the box. Excluded by default."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("AURIS_GPU_WORKERS", "1")

    from auris.server.app import create_app  # uses the production lifespan → build_pool

    with TestClient(create_app()) as c:
        c.headers.update({"Authorization": "Bearer secret"})
        r = c.post(
            "/v1/voices/Me/clone",
            files={"file": ("ref.wav", _real_wav(), "audio/wav")},
            data={"ref_text": "this is a reference recording for cloning"},
        )
        assert r.status_code == 201, r.text

        r = c.post("/v1/tts", json={"text": "Hello from the service.", "voice": "Me"})
        assert r.status_code == 200, r.text
        audio, sr = sf.read(_io.BytesIO(r.content), dtype="float32")
        assert sr == 24000 and len(audio) > sr  # produced >1s of audio
