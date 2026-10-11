from __future__ import annotations

import io as _io
import numpy as np
import soundfile as sf


def _wav_bytes(seconds=4, sr=22050):
    buf = _io.BytesIO()
    sf.write(buf, np.zeros(seconds * sr, dtype=np.float32), sr, format="WAV")
    return buf.getvalue()


def test_clone_list_delete_roundtrip(client):
    # the conftest's FakeAsrPool supplies the transcript, so no whisper/model is needed
    r = client.post("/v1/voices/Me/clone", files={"file": ("me.wav", _wav_bytes(), "audio/wav")})
    assert r.status_code == 201, r.text
    assert r.json()["voice"] == "Me"

    r = client.get("/v1/voices")
    assert r.status_code == 200
    names = [v["name"] for v in r.json()]
    assert "Me" in names

    r = client.delete("/v1/voices/Me")
    assert r.status_code == 200
    assert "Me" not in [v["name"] for v in client.get("/v1/voices").json()]


def test_voices_list_includes_presets(client):
    r = client.get("/v1/voices")
    assert r.status_code == 200
    voices = r.json()
    presets = [v for v in voices if v["kind"] == "preset"]
    assert {"narrator-us-f", "narrator-uk-f"} <= {p["name"] for p in presets}
    assert all(p["default_model"] == "kokoro" and p["description"] for p in presets)


def test_clone_rejects_reserved_preset_name(client):
    r = client.post(
        "/v1/voices/narrator-uk-f/clone",
        files={"file": ("me.wav", _wav_bytes(), "audio/wav")},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_clone_requires_auth(client):
    r = client.post(
        "/v1/voices/Me/clone",
        headers={"Authorization": ""},
        files={"file": ("me.wav", _wav_bytes(), "audio/wav")},
    )
    assert r.status_code == 401


def test_clone_rejects_undecodable_upload(client):
    r = client.post(
        "/v1/voices/Me/clone", files={"file": ("x.wav", b"this is not audio", "audio/wav")}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_audio"


def test_clone_rejects_oversized_upload(client, monkeypatch):
    import auris.server.routers.voices as voices_mod

    monkeypatch.setattr(voices_mod, "_MAX_UPLOAD_BYTES", 10)  # tiny cap
    r = client.post("/v1/voices/Me/clone", files={"file": ("x.wav", b"x" * 100, "audio/wav")})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_audio"


def _clone(client, name, seconds=4):
    return client.post(
        f"/v1/voices/{name}/clone",
        files={"file": ("me.wav", _wav_bytes(seconds=seconds), "audio/wav")},
        data={"ref_text": "hello world"},
    )


def test_clips_list_shape(client):
    assert _clone(client, "Cards").status_code == 201
    assert _clone(client, "Cards").status_code == 201
    r = client.get("/v1/voices/Cards/clips")
    assert r.status_code == 200, r.text
    clips = r.json()
    assert [c["index"] for c in clips] == [1, 2]
    assert clips[0]["transcript"] == "hello world"
    assert clips[0]["filename"] == "clip_001.wav" and clips[0]["duration_s"] > 0


def test_delete_a_clip_removes_row_and_file(client):
    _clone(client, "Trim")
    _clone(client, "Trim")
    r = client.delete("/v1/voices/Trim/clips/1")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1, "remaining": 1}
    assert [c["index"] for c in client.get("/v1/voices/Trim/clips").json()] == [2]
    settings = client.app.state.settings
    voices_dir = settings.voices_dir
    assert not any(p.name == "clip_001.wav" for p in voices_dir.rglob("clip_*.wav"))


def test_deleting_the_last_clip_is_refused(client):
    _clone(client, "Solo")
    r = client.delete("/v1/voices/Solo/clips/1")
    assert r.status_code == 422
    assert "delete the voice" in r.json()["error"]["message"]


def test_delete_unknown_clip_is_404(client):
    _clone(client, "Gone")
    assert client.delete("/v1/voices/Gone/clips/9").status_code == 404
    assert client.get("/v1/voices/Nobody/clips").status_code == 404


def test_rename_voice(client):
    _clone(client, "Old")
    r = client.patch("/v1/voices/Old", json={"name": "New"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "New"
    names = [v["name"] for v in client.get("/v1/voices").json()]
    assert "New" in names and "Old" not in names


def test_rename_collision_is_409_and_preset_is_422(client):
    _clone(client, "A")
    _clone(client, "B")
    assert client.patch("/v1/voices/A", json={"name": "b"}).status_code == 409
    assert client.patch("/v1/voices/A", json={"name": "narrator-us-f"}).status_code == 422
    assert client.patch("/v1/voices/narrator-us-f", json={"name": "x"}).status_code == 404


def test_clips_endpoints_handle_preset_name_without_500(client):
    assert client.get("/v1/voices/narrator-us-f/clips").status_code == 404
    assert client.delete("/v1/voices/narrator-us-f/clips/1").status_code == 404


def test_unknown_clip_and_unknown_voice_have_distinct_codes(client):
    _clone(client, "Codes")
    assert client.delete("/v1/voices/Codes/clips/9").json()["error"]["code"] == "clip_not_found"
    assert client.get("/v1/voices/Nobody/clips").json()["error"]["code"] == "voice_not_found"


def test_names_that_cannot_round_trip_a_url_are_422(client):
    _clone(client, "Url")
    for bad in ("a/b", "x\ny", "z" * 65):
        r = client.patch("/v1/voices/Url", json={"name": bad})
        assert r.status_code == 422, (bad, r.text)
    # a preset name is refused however it is spelled
    assert client.patch("/v1/voices/Url", json={"name": " Narrator-US-F "}).status_code == 422
