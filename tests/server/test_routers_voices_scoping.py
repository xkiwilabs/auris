"""The cross-user matrix. This is the test that would have caught the original defect:
before this feature, a non-admin could PATCH another account's voice and get 200.
"""

from __future__ import annotations

import pytest

from tests.server.test_voices import _wav_bytes

# Fixtures come from tests/server/conftest.py. Note their real shapes:
#   seed_user(email, role="user") -> User      (a User object, not an email)
#   as_user(user) -> client                    (takes the User, returns the same TestClient)
#   seed_voice(name, owner_user, visibility)


@pytest.mark.parametrize(
    "visibility,expected_status",
    [("shared", 403), ("private", 404)],
    ids=["shared-is-forbidden", "private-is-invisible"],
)
def test_another_users_voice_cannot_be_renamed(
    seed_user, seed_voice, as_user, visibility, expected_status
):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility=visibility)
    c = as_user(alice)
    assert c.patch("/v1/voices/bobs-voice", json={"name": "mine-now"}).status_code == (
        expected_status
    )


@pytest.mark.parametrize(
    "visibility,expected_status",
    [("shared", 403), ("private", 404)],
    ids=["shared-is-forbidden", "private-is-invisible"],
)
def test_another_users_voice_cannot_be_deleted(
    seed_user, seed_voice, as_user, visibility, expected_status
):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility=visibility)
    assert as_user(alice).delete("/v1/voices/bobs-voice").status_code == expected_status


def test_a_clip_of_another_users_voice_cannot_be_deleted(seed_user, seed_voice, as_user):
    """The add/remove-clip path, which the spec's matrix covers and the routes above do not."""
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    assert as_user(alice).delete("/v1/voices/bobs-voice/clips/0").status_code == 403
    # The name promises the clip survived the refusal, not just that the refusal happened.
    clips = as_user(bob).get("/v1/voices/bobs-voice/clips").json()
    assert len(clips) == 1


def test_a_shared_voice_is_listed_and_a_private_one_is_not(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-shared", bob, visibility="shared")
    seed_voice("bobs-secret", bob, visibility="private")
    names = [v["name"] for v in as_user(alice).get("/v1/voices").json()]
    assert "bobs-shared" in names
    assert "bobs-secret" not in names


def test_an_admin_sees_and_may_rename_anything(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    boss = seed_user("boss@example.com", role="admin")
    seed_voice("bobs-secret", bob, visibility="private")
    c = as_user(boss)
    assert "bobs-secret" in [v["name"] for v in c.get("/v1/voices").json()]
    assert c.patch("/v1/voices/bobs-secret", json={"name": "tidied"}).status_code == 200


def test_the_owner_may_do_everything_to_their_own(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice, visibility="private")
    c = as_user(alice)
    assert c.patch("/v1/voices/mine", json={"visibility": "shared"}).status_code == 200
    # A 200 that wrote nothing would still pass without this: read the row back and check the
    # field actually changed, not just that the route returned success.
    mine = next(v for v in c.get("/v1/voices").json() if v["name"] == "mine")
    assert mine["visibility"] == "shared"
    assert c.get("/v1/voices/mine/clips").status_code == 200
    assert c.delete("/v1/voices/mine").status_code == 200


def test_listing_reports_owner_and_visibility(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice, visibility="shared")
    rows = as_user(alice).get("/v1/voices").json()
    mine = next(v for v in rows if v["name"] == "mine")
    assert mine["owner"] == "alice@example.com"
    assert mine["visibility"] == "shared"
    preset = next(v for v in rows if v["kind"] == "preset")
    assert preset["owner"] is None and preset["visibility"] is None


def test_a_legacy_voice_lists_with_no_owner(client, seed_user, as_user):
    """Unowned rows must render, not crash the owner-email join on a None key."""
    from tests.server.conftest import run_on_app_loop

    alice = seed_user("alice@example.com")
    run_on_app_loop(
        client,
        client.app.state.db.execute(
            "INSERT INTO speakers (id, name, created_at, default_model, default_speed,"
            " owner_id, visibility) VALUES ('l', 'old', 'then', 'f5_tts', 1.0, NULL, 'shared')"
        ),
    )
    row = next(v for v in as_user(alice).get("/v1/voices").json() if v["name"] == "old")
    assert row["owner"] is None
    assert row["visibility"] == "shared"


def test_visibility_must_be_private_or_shared(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    assert as_user(alice).patch("/v1/voices/mine", json={"visibility": "public"}).status_code == 422


def _seed_unowned(client, name, visibility):
    """A legacy row with no owner — `seed_voice` always has one, and this is the only shape
    that reaches the inert-visibility rule."""
    from tests.server.conftest import run_on_app_loop

    run_on_app_loop(
        client,
        client.app.state.db.execute(
            "INSERT INTO speakers (id, name, created_at, default_model, default_speed,"
            " owner_id, visibility) VALUES (?, ?, 'then', 'f5_tts', 1.0, NULL, ?)",
            (name, name, visibility),
        ),
    )


def test_making_an_unowned_voice_private_is_422(client):
    """`speakers._VISIBLE`'s `s.owner_id IS NULL` clause shows an unowned row to every account
    whatever its visibility column says, so writing `private` there changes a column and
    nothing a caller could observe. The refusal has to live here, not only in the CLI: the
    remote `auris voices unshare` PATCHed this and printed `legacy: private`, exit 0, while the
    same command on the box refused. `client` carries the master token, i.e. an admin — the only
    principal that may write an unowned voice at all."""
    _seed_unowned(client, "legacy", "shared")
    r = client.patch("/v1/voices/legacy", json={"visibility": "private"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request"
    assert "no owner" in body["message"] and "adopt" in body["message"]
    row = next(v for v in client.get("/v1/voices").json() if v["name"] == "legacy")
    assert row["visibility"] == "shared"  # the row, not just the status code


def test_making_an_unowned_voice_shared_is_still_allowed(client):
    """Only `private` is inert. `share` stays a 200 so it matches the local CLI, which succeeds
    with a note rather than refusing — and so adopting later leaves a sane column behind."""
    _seed_unowned(client, "legacy2", "private")
    assert client.patch("/v1/voices/legacy2", json={"visibility": "shared"}).status_code == 200
    row = next(v for v in client.get("/v1/voices").json() if v["name"] == "legacy2")
    assert row["visibility"] == "shared"


def test_a_patch_of_visibility_on_another_users_shared_voice_is_403(seed_user, seed_voice, as_user):
    """Every other 403 test patches `name`; this is the only one that drives update_voice's
    visibility branch through get_for_write — a reviewer found this path was untested."""
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    r = as_user(alice).patch("/v1/voices/bobs-voice", json={"visibility": "private"})
    assert r.status_code == 403


def test_an_empty_patch_body_is_refused(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    assert as_user(alice).patch("/v1/voices/mine", json={}).status_code == 422


def test_owner_disambiguates_a_duplicated_shared_name(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    bob = seed_user("bob@example.com")
    carol = seed_user("carol@example.com")
    seed_voice("narrator", alice, visibility="shared")
    seed_voice("narrator", bob, visibility="shared")
    c = as_user(carol)
    ambiguous = c.get("/v1/voices/narrator/clips")
    assert ambiguous.status_code == 409
    # The message must tell the caller to use the parameter the route actually accepts (an
    # email), and must not leak either account's internal user id to a caller who can merely
    # see that two people share a name.
    message = ambiguous.json()["error"]["message"]
    assert "owner=<email>" in message
    assert alice.id not in message
    assert bob.id not in message
    ok = c.get("/v1/voices/narrator/clips", params={"owner": "bob@example.com"})
    assert ok.status_code == 200


def test_owner_cannot_reach_a_voice_you_may_not_see(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("narrator", bob, visibility="private")
    r = as_user(alice).get("/v1/voices/narrator/clips", params={"owner": "bob@example.com"})
    assert r.status_code == 404


def test_an_unknown_owner_email_is_404_not_422(seed_user, seed_voice, as_user):
    """422 would tell an unauthenticated-to-that-account caller which addresses have accounts."""
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    r = as_user(alice).get("/v1/voices/mine/clips", params={"owner": "ghost@example.com"})
    assert r.status_code == 404


def test_an_owned_key_may_clone_create_append_patch_and_delete_its_owners_voice(seed_user, as_key):
    """The coverage gap the review found: there was no test of a `key` principal against any
    voice route in any tier. An owned, non-admin key must behave exactly like its owner would."""
    alice = seed_user("alice@example.com")
    c = as_key(alice)
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}

    r = c.post("/v1/voices/keyed/clone", files=files, data={"ref_text": "hello there"})
    assert r.status_code == 201, r.text  # create
    r = c.post("/v1/voices/keyed/clone", files=files, data={"ref_text": "hello again"})
    assert r.status_code == 201, r.text  # append
    assert len(c.get("/v1/voices/keyed/clips").json()) == 2

    assert c.patch("/v1/voices/keyed", json={"visibility": "shared"}).status_code == 200
    assert c.delete("/v1/voices/keyed").status_code == 200


def test_an_owned_key_cannot_clone_onto_another_accounts_shared_name(seed_user, seed_voice, as_key):
    """A *shared* name owned by someone else is a 403 on clone, same as get_for_write — see
    test_cloning_onto_another_users_name_does_not_touch_their_voice below for the equivalent
    as a `user` principal. (A *private* name owned by someone else is invisible, not an error:
    the clone creates the caller's own distinct voice of that name, which is the find_for_clone
    contract — pinned by
    test_cloning_onto_a_name_another_account_holds_privately_creates_your_own below, not a
    permission refusal.)"""
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    c = as_key(alice)
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = c.post("/v1/voices/bobs-voice/clone", files=files, data={"ref_text": "hello there"})
    assert r.status_code == 403
    clips = as_key(bob).get("/v1/voices/bobs-voice/clips").json()
    assert len(clips) == 1


def test_a_refused_clone_runs_no_asr_decode(client, seed_user, seed_voice, as_key):
    """The refusal has to come *before* the audio work, not after it. With no `ref_text` the
    route reads the upload, decodes, resamples and runs a full whisper decode on the GPU — so a
    403 that arrives afterwards has already spent all of it. `FakeAsrPool` records every job it
    is handed; zero is the assertion that matters, and asserting only the 403 would pass against
    the defect. Same principle as `pool.admit()`: a caller that would be rejected anyway never
    allocates a decoded float32 array."""
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    pool = client.app.state.asr_pool
    pool.jobs.clear()

    c = as_key(alice)
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = c.post("/v1/voices/bobs-voice/clone", files=files)  # no ref_text => would decode

    assert r.status_code == 403, r.text
    assert pool.jobs == [], "a refused clone must not submit an ASR job"
    # The pool is live for a clone that *is* allowed, so the assertion above is not vacuous.
    assert c.post("/v1/voices/mine/clone", files=files).status_code == 201
    assert len(pool.jobs) == 1


@pytest.mark.parametrize(
    "visibility,expected_status",
    [("shared", 403), ("private", 404)],
    ids=["shared-is-forbidden", "private-is-invisible"],
)
def test_an_owned_key_cannot_patch_or_delete_another_accounts_voice(
    seed_user, seed_voice, as_key, visibility, expected_status
):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility=visibility)
    c = as_key(alice)
    assert c.patch("/v1/voices/bobs-voice", json={"name": "mine-now"}).status_code == (
        expected_status
    )
    assert c.delete("/v1/voices/bobs-voice").status_code == expected_status


def test_an_ownerless_key_is_refused_at_clone_create_with_the_new_message(as_key):
    c = as_key()  # no owner
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = c.post("/v1/voices/brand-new/clone", files=files, data={"ref_text": "hello there"})
    assert r.status_code == 403
    message = r.json()["error"]["message"]
    assert "owner account" in message and "re-issue" in message


def test_an_ownerless_key_is_refused_at_clone_append_with_the_same_message(
    seed_user, seed_voice, as_key
):
    """The append path (an existing *shared* voice) must fail the same way as create — not with
    the generic "belongs to someone else" a reader would otherwise see."""
    bob = seed_user("bob@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    c = as_key()  # no owner
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = c.post("/v1/voices/bobs-voice/clone", files=files, data={"ref_text": "hello there"})
    assert r.status_code == 403
    message = r.json()["error"]["message"]
    assert "owner account" in message and "re-issue" in message


def test_an_ownerless_key_gets_the_same_message_on_patch_and_delete(seed_user, seed_voice, as_key):
    """get_for_write (PATCH/DELETE) must report the same actionable cause as clone for an
    ownerless caller — not the generic "belongs to someone else", which is false for an
    unowned voice and uninformative for anyone else's."""
    bob = seed_user("bob@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    c = as_key()  # no owner
    patch = c.patch("/v1/voices/bobs-voice", json={"name": "mine-now"})
    delete = c.delete("/v1/voices/bobs-voice")
    assert patch.status_code == 403 and delete.status_code == 403
    for r in (patch, delete):
        message = r.json()["error"]["message"]
        assert "owner account" in message and "re-issue" in message
        assert "belongs to someone else" not in message


def test_an_ownerless_key_can_still_list_voices(seed_user, seed_voice, as_key):
    bob = seed_user("bob@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    c = as_key()  # no owner
    r = c.get("/v1/voices")
    assert r.status_code == 200
    assert "bobs-voice" in [v["name"] for v in r.json()]


def test_cloning_onto_another_users_name_does_not_touch_their_voice(
    client, seed_user, seed_voice, as_user
):
    """Review Focus #2: get_or_create_speaker used to look names up globally, so this would
    have appended Alice's clip to Bob's voice."""
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("narrator", bob, visibility="shared")
    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = as_user(alice).post(
        "/v1/voices/narrator/clone", files=files, data={"ref_text": "hello there"}
    )
    assert r.status_code == 403
    # The name promises Bob's voice was untouched, not just that the clone was refused.
    clips = as_user(bob).get("/v1/voices/narrator/clips").json()
    assert len(clips) == 1


def test_cloning_onto_a_name_another_account_holds_privately_creates_your_own(
    client, seed_user, seed_voice, as_user
):
    """The branch's one reasoned departure from its plan, and until now its only untested
    behaviour. Review Focus #2 wanted a 404 here; a 404 when Alice creates a brand-new voice
    because an *invisible* row holds the name would reintroduce the name-squatting and
    existence-leak the per-owner namespace removed. So a private namesake is simply not there
    as far as Alice is concerned: she gets her own `narrator`, 201. A later "simplification" to
    404 or 403 would otherwise pass the whole suite.
    """
    from auris.voice_library import speakers

    from tests.server.conftest import run_on_app_loop

    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    bobs = seed_voice("narrator", bob, visibility="private")

    files = {"file": ("ref.wav", _wav_bytes(), "audio/wav")}
    r = as_user(alice).post(
        "/v1/voices/narrator/clone", files=files, data={"ref_text": "hello there"}
    )
    assert r.status_code == 201, r.text

    # Bob's voice is a different row, still his, still one clip.
    bobs_row = run_on_app_loop(client, speakers.get_speaker_by_id(client.app.state.db, bobs.id))
    assert bobs_row is not None and bobs_row.owner_id == bob.id
    assert len(as_user(bob).get("/v1/voices/narrator/clips").json()) == 1

    # And each account sees exactly one `narrator` — their own.
    for who, email in ((alice, "alice@example.com"), (bob, "bob@example.com")):
        rows = [v for v in as_user(who).get("/v1/voices").json() if v["name"] == "narrator"]
        assert [v["owner"] for v in rows] == [email]


# --- GET /v1/voices/{name}/clips/{index}/audio: hearing a stored reference clip --------------


def _clip_index(c, name):
    (clip,) = c.get(f"/v1/voices/{name}/clips").json()
    return clip["index"]


def test_the_owner_can_play_back_their_reference_clip(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice, visibility="private")
    c = as_user(alice)
    r = c.get(f"/v1/voices/mine/clips/{_clip_index(c, 'mine')}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WAVE"


@pytest.mark.parametrize(
    "visibility,expected_status",
    [("shared", 403), ("private", 404)],
    ids=["shared-is-refused", "private-is-invisible"],
)
def test_another_users_recording_is_never_playable(
    seed_user, seed_voice, as_user, visibility, expected_status
):
    # A shared voice is usable by everyone, but the raw recording of the person stays with its
    # owner: 403 for a voice the caller can already see, and a private one is still 404, never
    # 403, so the status code can't confirm that the name exists.
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility=visibility)
    index = _clip_index(as_user(bob), "bobs-voice")
    assert as_user(alice).get(f"/v1/voices/bobs-voice/clips/{index}/audio").status_code == (
        expected_status
    )


def test_a_shared_voices_transcripts_stay_readable_to_everyone(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("bobs-voice", bob, visibility="shared")
    assert as_user(alice).get("/v1/voices/bobs-voice/clips").status_code == 200


def test_an_admin_can_play_anyones_recording(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    boss = seed_user("boss@example.com", role="admin")
    seed_voice("bobs-voice", bob, visibility="shared")
    index = _clip_index(as_user(bob), "bobs-voice")
    assert as_user(boss).get(f"/v1/voices/bobs-voice/clips/{index}/audio").status_code == 200


def test_an_unowned_voices_recording_is_admin_only(seed_user, seed_voice, as_user):
    # Readable by every account, but nobody can prove they recorded it — the same rule as
    # changing it.
    alice = seed_user("alice@example.com")
    seed_voice("legacy", None, visibility="shared")
    index = _clip_index(as_user(alice), "legacy")
    assert as_user(alice).get(f"/v1/voices/legacy/clips/{index}/audio").status_code == 403


def test_a_clip_that_does_not_exist_is_a_404(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    assert as_user(alice).get("/v1/voices/mine/clips/999/audio").status_code == 404


def test_playing_a_clip_needs_a_signed_in_caller(seed_user, seed_voice, as_user):
    alice = seed_user("alice@example.com")
    seed_voice("mine", alice)
    c = as_user(alice)
    index = _clip_index(c, "mine")
    c.cookies.clear()
    assert c.get(f"/v1/voices/mine/clips/{index}/audio").status_code == 401
