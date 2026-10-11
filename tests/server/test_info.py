from __future__ import annotations

from auris.audio.asr_models import DEFAULT_MODEL


def test_info_shape(client):
    r = client.get("/v1/info")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"]
    assert body["pipelines"] == {"stt": True, "tts": True}
    assert set(body["tts"]) == {"f5_tts", "kokoro"}
    assert body["tts"]["f5_tts"]["workers"] == 1
    assert isinstance(body["tts"]["f5_tts"]["devices"], list)
    asr = body["asr"]
    assert asr["model"] == DEFAULT_MODEL and asr["workers"] >= 1
    assert asr["device"] in ("cpu", "cuda") and asr["compute_type"]
    assert "small.en" in asr["allowed"]
    limits = body["limits"]
    assert limits["max_input_chars"] == 2000 and limits["asr_max_upload_mb"] == 250


def test_info_reports_disabled_pipelines(client):
    client.app.state.pools = {}
    client.app.state.asr_pool = None
    body = client.get("/v1/info").json()
    assert body["pipelines"] == {"stt": False, "tts": False}
    assert body["tts"] == {} and body["asr"]["workers"] == 0


def test_info_needs_auth(client):
    assert client.get("/v1/info", headers={"Authorization": ""}).status_code == 401
