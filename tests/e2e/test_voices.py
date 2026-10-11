"""The voice library over HTTP: cloning (with and without a transcript), clips, rename, delete."""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
import soundfile as sf

from tests.e2e.audio import REFERENCE_TEXT, reference_wav, wer
from tests.e2e.server import SEEDED_VOICE

pytestmark = pytest.mark.e2e


@pytest.fixture
def voice(admin):
    """A throwaway cloned voice, deleted afterwards whatever the test did to it."""
    name = "tmp-voice"
    wav, _ = reference_wav()
    admin.delete(f"/v1/voices/{name}")  # in case a previous failure leaked one
    r = admin.post(
        f"/v1/voices/{name}/clone",
        files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
        data={"ref_text": REFERENCE_TEXT},
    )
    assert r.status_code == 201, r.text
    yield name
    with contextlib.suppress(Exception):
        admin.delete(f"/v1/voices/{name}")
        admin.delete("/v1/voices/renamed-voice")  # the rename test moves it


def test_presets_are_listed_and_marked(admin):
    voices = admin.get("/v1/voices").json()
    presets = {v["name"] for v in voices if v["kind"] == "preset"}
    assert "narrator-uk-f" in presets
    assert {v["name"] for v in voices if v["kind"] == "cloned"} >= {SEEDED_VOICE}


def test_clone_with_ref_text_echoes_it_and_caps_at_ten_seconds(admin, voice):
    clips = admin.get(f"/v1/voices/{voice}/clips").json()
    assert len(clips) == 1
    assert clips[0]["transcript"] == REFERENCE_TEXT
    # prepare_reference caps references at 10 s — deliberately, see docs/architecture.md.
    assert 3.0 <= clips[0]["duration_s"] <= 10.0


def test_clone_without_ref_text_transcribes_the_upload(admin):
    """The clone path runs the real whisper pool. Looser than the clean round trip: the audio
    is trimmed and capped to 10 s first, so the tail of the sentence may not be there."""
    wav, text = reference_wav()
    name = "auto-transcribed"
    admin.delete(f"/v1/voices/{name}")
    try:
        r = admin.post(
            f"/v1/voices/{name}/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 201, r.text
        transcript = r.json()["transcript"]
        assert transcript.strip()
        # Compare against the words that survived the cap: whisper's output should match the
        # start of the source sentence, not the whole of it.
        spoken = text.split()
        heard = transcript.split()
        assert wer(" ".join(spoken[: len(heard)]), transcript) <= 0.3
    finally:
        admin.delete(f"/v1/voices/{name}")


def test_clone_rejects_short_audio_and_preset_names(admin, tmp_path):
    short = tmp_path / "short.wav"
    sf.write(short, np.zeros(24000, dtype="float32"), 24000)  # 1 s, under the 3 s floor
    r = admin.post(
        "/v1/voices/too-short/clone",
        files={"file": ("short.wav", short.read_bytes(), "audio/wav")},
        data={"ref_text": "hello"},
    )
    assert r.status_code == 422 and "at least" in r.json()["error"]["message"]

    wav, _ = reference_wav()
    r = admin.post(
        "/v1/voices/narrator-uk-f/clone",
        files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
        data={"ref_text": REFERENCE_TEXT},
    )
    assert r.status_code == 422 and "preset" in r.json()["error"]["message"]


def test_clips_can_be_added_and_the_middle_one_deleted(admin, voice):
    wav, _ = reference_wav()
    for _ in range(2):
        r = admin.post(
            f"/v1/voices/{voice}/clone",
            files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
            data={"ref_text": REFERENCE_TEXT},
        )
        assert r.status_code == 201
    clips = admin.get(f"/v1/voices/{voice}/clips").json()
    assert [c["index"] for c in clips] == [1, 2, 3]

    r = admin.delete(f"/v1/voices/{voice}/clips/2")
    assert r.status_code == 200 and r.json() == {"deleted": 2, "remaining": 2}
    assert [c["index"] for c in admin.get(f"/v1/voices/{voice}/clips").json()] == [1, 3]

    missing = admin.delete(f"/v1/voices/{voice}/clips/99")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "clip_not_found"


def test_the_last_clip_cannot_be_deleted(admin, voice):
    """A voice with no clips cannot synthesise, so the API refuses to create that state."""
    r = admin.delete(f"/v1/voices/{voice}/clips/1")
    assert r.status_code == 422
    assert "only clip" in r.json()["error"]["message"]
    assert len(admin.get(f"/v1/voices/{voice}/clips").json()) == 1


def test_rename_and_its_two_refusals(admin, voice):
    r = admin.patch(f"/v1/voices/{voice}", json={"name": "renamed-voice"})
    assert r.status_code == 200 and r.json()["name"] == "renamed-voice"
    assert admin.get(f"/v1/voices/{voice}/clips").status_code == 404
    assert admin.get("/v1/voices/renamed-voice/clips").status_code == 200

    assert admin.patch("/v1/voices/renamed-voice", json={"name": SEEDED_VOICE}).status_code == 409
    assert admin.patch("/v1/voices/renamed-voice", json={"name": "calm-m"}).status_code == 422


def test_deleting_a_voice_removes_its_files(admin, server, voice):
    voices_dir = server.data_dir / "voices"
    before = {p.name for p in voices_dir.iterdir()} if voices_dir.exists() else set()
    assert admin.delete(f"/v1/voices/{voice}").status_code == 200
    assert admin.get(f"/v1/voices/{voice}/clips").status_code == 404
    after = {p.name for p in voices_dir.iterdir()} if voices_dir.exists() else set()
    # The speaker's whole directory goes, not just the row.
    assert len(after) == len(before) - 1
