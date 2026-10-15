"""A blended voice through the real Kokoro model: it speaks clearly, and it is not either parent."""

from __future__ import annotations

import contextlib

import numpy as np
import pytest

from tests.e2e.audio import assert_audible, wer

pytestmark = pytest.mark.e2e

TEXT = "A blended voice should still read this sentence clearly."


@pytest.fixture
def blend(admin):
    name = "tmp-blend"
    admin.delete(f"/v1/voices/{name}")  # in case a previous failure leaked one
    r = admin.put(
        f"/v1/voices/{name}/blend",
        json={
            "components": [
                {"voice": "narrator-uk-f", "weight": 50},
                {"voice": "deep-uk-m", "weight": 50},
            ]
        },
    )
    assert r.status_code == 201, r.text
    yield name
    with contextlib.suppress(Exception):
        admin.delete(f"/v1/voices/{name}")


def _speak(admin, voice, tmp_path):
    r = admin.post("/v1/tts", json={"voice": voice, "text": TEXT, "format": "wav"})
    assert r.status_code == 200, r.text
    out = tmp_path / f"{voice}.wav"
    out.write_bytes(r.content)
    return assert_audible(out), r.content


def test_a_blend_speaks_intelligibly_and_differs_from_both_parents(admin, blend, tmp_path):
    mixed, wav = _speak(admin, blend, tmp_path)
    r = admin.post(
        "/v1/audio/transcriptions",
        files={"file": ("blend.wav", wav, "audio/wav")},
        data={"response_format": "text"},
    )
    assert r.status_code == 200, r.text
    error = wer(TEXT, r.text)
    assert error <= 0.2, f"WER {error:.2f}\n  said:  {TEXT}\n  heard: {r.text}"

    for parent in ("narrator-uk-f", "deep-uk-m"):
        audio, _ = _speak(admin, parent, tmp_path)
        n = min(len(audio), len(mixed))
        same = len(audio) == len(mixed) and np.allclose(audio[:n], mixed[:n], atol=1e-3)
        assert not same, f"the blend sounds exactly like {parent}: the mix was not applied"


def test_re_mixing_changes_the_voice(admin, blend, tmp_path):
    before, _ = _speak(admin, blend, tmp_path)
    r = admin.put(
        f"/v1/voices/{blend}/blend",
        json={
            "components": [
                {"voice": "narrator-uk-f", "weight": 90},
                {"voice": "deep-uk-m", "weight": 10},
            ]
        },
    )
    assert r.status_code == 200, r.text
    after, _ = _speak(admin, blend, tmp_path)
    assert not (len(before) == len(after) and np.allclose(before, after, atol=1e-3))
