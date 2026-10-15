"""Blended voices over HTTP: PUT /v1/voices/{name}/blend, the listing, and synthesis routing."""

from __future__ import annotations

import numpy as np

from auris.server.pool import SynthesisPool
from tests.server.test_voices import _wav_bytes

MIX = {
    "components": [{"voice": "narrator-uk-f", "weight": 60}, {"voice": "bright-uk-f", "weight": 40}]
}


def _listed(c, name):
    return next(v for v in c.get("/v1/voices").json() if v["name"] == name)


def test_put_creates_a_blend_then_re_mixes_it(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    r = c.put("/v1/voices/warm/blend", json=MIX)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "blend" and body["clips"] == 0
    assert body["default_model"] == "kokoro"
    assert body["description"] == "Blend: 60% narrator-uk-f + 40% bright-uk-f"
    assert body["blend"] == [
        {"voice": "narrator-uk-f", "weight": 0.6},
        {"voice": "bright-uk-f", "weight": 0.4},
    ]
    assert body["visibility"] == "private" and body["owner"] == "alice@example.com"

    remix = {
        "components": [{"voice": "narrator-uk-f", "weight": 1}, {"voice": "deep-uk-m", "weight": 3}]
    }
    r = c.put("/v1/voices/warm/blend", json={**remix, "visibility": "shared"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Blend: 25% narrator-uk-f + 75% deep-uk-m"
    assert r.json()["visibility"] == "shared"
    listed = _listed(c, "warm")
    assert listed["kind"] == "blend" and listed["blend"][1] == {
        "voice": "deep-uk-m",
        "weight": 0.75,
    }


def test_a_re_mix_without_visibility_keeps_it(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    c.put("/v1/voices/warm/blend", json={**MIX, "visibility": "shared"})
    assert c.put("/v1/voices/warm/blend", json=MIX).json()["visibility"] == "shared"


def test_bad_recipes_are_422(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    one = {"components": [{"voice": "narrator-uk-f", "weight": 1}]}
    four = {
        "components": [
            {"voice": n, "weight": 1}
            for n in ("narrator-uk-f", "bright-uk-f", "deep-uk-m", "calm-m")
        ]
    }
    dup = {"components": [{"voice": "calm-m", "weight": 1}, {"voice": "CALM-M", "weight": 1}]}
    zero = {"components": [{"voice": "calm-m", "weight": 0}, {"voice": "deep-uk-m", "weight": 1}]}
    for body in (one, four, dup, zero):
        assert c.put("/v1/voices/x/blend", json=body).status_code == 422, body
    assert "x" not in [v["name"] for v in c.get("/v1/voices").json()]


def test_only_built_in_voices_can_be_blended(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("cloned-one", alice)
    body = {"components": [{"voice": "cloned-one", "weight": 1}, {"voice": "calm-m", "weight": 1}]}
    r = as_user(alice).put("/v1/voices/x/blend", json=body)
    assert r.status_code == 422
    assert "built-in" in r.json()["error"]["message"]


def test_a_preset_name_cannot_be_a_blend(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    assert c.put("/v1/voices/calm-m/blend", json=MIX).status_code == 422


def test_a_cloned_voice_cannot_become_a_blend(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    c = as_user(alice)
    r = c.put("/v1/voices/mine/blend", json=MIX)
    assert r.status_code == 422
    assert _listed(c, "mine")["kind"] == "cloned"


def test_a_clip_cannot_be_added_to_a_blend(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    c.put("/v1/voices/warm/blend", json=MIX)
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = c.post("/v1/voices/warm/clone", files=files, data={"ref_text": "hello there"})
    assert r.status_code == 422
    assert "blended" in r.json()["error"]["message"]
    assert _listed(c, "warm")["clips"] == 0


def test_another_accounts_shared_name_is_403_and_untouched(seed_user, as_user):
    bob, alice = seed_user("bob@example.com"), seed_user("alice@example.com")
    as_user(bob).put("/v1/voices/warm/blend", json={**MIX, "visibility": "shared"})
    other = {"components": [{"voice": "calm-m", "weight": 1}, {"voice": "deep-uk-m", "weight": 1}]}
    assert as_user(alice).put("/v1/voices/warm/blend", json=other).status_code == 403
    assert _listed(as_user(bob), "warm")["blend"][0]["voice"] == "narrator-uk-f"


def test_another_accounts_private_name_gives_you_your_own(seed_user, as_user):
    bob, alice = seed_user("bob@example.com"), seed_user("alice@example.com")
    as_user(bob).put("/v1/voices/warm/blend", json=MIX)
    other = {"components": [{"voice": "calm-m", "weight": 1}, {"voice": "deep-uk-m", "weight": 1}]}
    r = as_user(alice).put("/v1/voices/warm/blend", json=other)
    assert r.status_code == 201 and r.json()["owner"] == "alice@example.com"
    assert _listed(as_user(bob), "warm")["blend"][0]["voice"] == "narrator-uk-f"


def test_an_admin_re_mixes_someone_elses_blend_in_place(seed_user, as_user):
    bob, root = seed_user("bob@example.com"), seed_user("root@example.com", role="admin")
    as_user(bob).put("/v1/voices/warm/blend", json=MIX)
    other = {"components": [{"voice": "calm-m", "weight": 1}, {"voice": "deep-uk-m", "weight": 1}]}
    r = as_user(root).put("/v1/voices/warm/blend", json=other)
    assert r.status_code == 200 and r.json()["owner"] == "bob@example.com"


def test_an_ownerless_key_cannot_blend(as_key):
    assert as_key().put("/v1/voices/warm/blend", json=MIX).status_code == 403


def test_an_owned_key_blends_for_its_owner(seed_user, as_key):
    alice = seed_user("alice@example.com")
    r = as_key(alice).put("/v1/voices/warm/blend", json=MIX)
    assert r.status_code == 201 and r.json()["owner"] == "alice@example.com"


def test_a_blend_can_be_renamed_and_deleted(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    c.put("/v1/voices/warm/blend", json=MIX)
    r = c.patch("/v1/voices/warm", json={"name": "warmer"})
    assert r.status_code == 200 and r.json()["kind"] == "blend"
    assert c.delete("/v1/voices/warmer").status_code == 200
    assert "warmer" not in [v["name"] for v in c.get("/v1/voices").json()]


class _Recorder:
    """A kokoro stand-in that remembers the conditioning it was handed."""

    sample_rate = 24000
    name = "kokoro"
    device = "cpu"

    def __init__(self):
        self.seen = []

    def load(self):
        pass

    def synthesise(self, text, conditioning, speed=1.0):
        self.seen.append(conditioning)
        return np.full(8, 0.1, dtype=np.float32)


def test_a_blend_synthesises_on_kokoro_with_its_mix(client, seed_user, as_user):
    rec = _Recorder()
    client.app.state.pools["kokoro"] = SynthesisPool([rec], queue_maxsize=8)
    c = as_user(seed_user("alice@example.com"))
    c.put("/v1/voices/warm/blend", json=MIX)
    r = c.post("/v1/tts", json={"text": "hi", "voice": "warm", "format": "wav"})
    assert r.status_code == 200, r.text
    (cond,) = rec.seen
    assert cond.voice_id == "bf_emma" and cond.lang_code == "b"  # the heavier component
    assert [v for v, _ in cond.mix] == ["bf_emma", "bf_isabella"]
    assert [round(w, 6) for _, w in cond.mix] == [0.6, 0.4]


def test_owner_picks_which_accounts_blend_an_admin_re_mixes(seed_user, as_user):
    """An admin holding a blend of the same name must still be able to edit someone else's."""
    bob, root = seed_user("bob@example.com"), seed_user("root@example.com", role="admin")
    as_user(root).put("/v1/voices/warm/blend", json=MIX)  # the admin's own "warm" (private)
    as_user(bob).put("/v1/voices/warm/blend", json=MIX)  # bob can't see it, so gets his own
    admin = as_user(root)
    other = {"components": [{"voice": "calm-m", "weight": 1}, {"voice": "deep-uk-m", "weight": 1}]}
    r = admin.put("/v1/voices/warm/blend?owner=bob@example.com", json=other)
    assert r.status_code == 200 and r.json()["owner"] == "bob@example.com"
    mine = [v for v in admin.get("/v1/voices").json() if v["name"] == "warm"]
    by_owner = {v["owner"]: v["blend"][0]["voice"] for v in mine}
    assert by_owner == {"bob@example.com": "calm-m", "root@example.com": "narrator-uk-f"}


def test_owner_follows_the_patch_rules(seed_user, as_user):
    bob, alice = seed_user("bob@example.com"), seed_user("alice@example.com")
    as_user(bob).put("/v1/voices/priv/blend", json=MIX)
    as_user(bob).put("/v1/voices/pub/blend", json={**MIX, "visibility": "shared"})
    c = as_user(alice)
    assert c.put("/v1/voices/priv/blend?owner=bob@example.com", json=MIX).status_code == 404
    assert c.put("/v1/voices/pub/blend?owner=bob@example.com", json=MIX).status_code == 403
    assert c.put("/v1/voices/pub/blend?owner=nobody@example.com", json=MIX).status_code == 404
