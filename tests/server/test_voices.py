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


def _speech_wav(spec, sr=24000):
    """Tone bursts ("s") and quiet pauses ("p") as a WAV, plus Words timed to the bursts."""
    from auris.audio.transcript import Word

    parts, words, t = [], [], 0.0
    for kind, seconds, word in spec:
        n = int(seconds * sr)
        if kind == "s":
            parts.append(0.3 * np.sin(2 * np.pi * 180 * np.arange(n) / sr))
            words.append(Word(start=t, end=t + seconds, word=word, probability=1.0))
        else:
            parts.append(1e-4 * np.random.default_rng(0).standard_normal(n))
        t += seconds
    buf = _io.BytesIO()
    sf.write(buf, np.concatenate(parts).astype(np.float32), sr, format="WAV")
    return buf.getvalue(), words


_LONG = [
    ("s", 4.0, "One."),
    ("p", 0.4, None),
    ("s", 4.0, "Two."),
    ("p", 0.4, None),
    ("s", 4.0, "Three."),
]


def test_a_long_upload_is_cut_in_a_pause_and_stores_only_the_words_kept(client):
    from .conftest import FakeAsrPool

    wav, words = _speech_wav(_LONG)
    pool = FakeAsrPool(text="One. Two. Three.", words=words)
    client.app.state.asr_pool = pool
    r = client.post("/v1/voices/Long/clone", files={"file": ("l.wav", wav, "audio/wav")})
    assert r.status_code == 201, r.text
    body = r.json()
    # Cut in the pause after "Two." — not at a blind 10.0 s, which lands inside "Three."
    assert body["transcript"] == "One. Two."
    assert 8.4 < body["duration_s"] <= 8.75
    assert body["warnings"] == []
    (job,) = pool.jobs
    o = job.options
    assert (o.language, o.vad_filter, o.word_timestamps) == ("en", False, True)


def test_a_typed_transcript_for_a_recording_that_must_be_cut_is_refused(client):
    wav, _ = _speech_wav(_LONG)
    r = client.post(
        "/v1/voices/Long/clone",
        files={"file": ("l.wav", wav, "audio/wav")},
        data={"ref_text": "One. Two. Three."},
    )
    assert r.status_code == 422
    assert "typed transcript would not match" in r.json()["error"]["message"]


def test_a_short_clip_is_stored_with_a_warning(client):
    from .conftest import FakeAsrPool

    wav, words = _speech_wav([("p", 0.3, None), ("s", 4.0, "Short."), ("p", 0.5, None)])
    client.app.state.asr_pool = FakeAsrPool(text="Short.", words=words)
    r = client.post("/v1/voices/Brief/clone", files={"file": ("b.wav", wav, "audio/wav")})
    assert r.status_code == 201, r.text
    assert any("usually captures the voice" in w for w in r.json()["warnings"])


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
