from __future__ import annotations

import pytest


def test_you_cannot_synthesise_with_another_users_private_voice(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("secret", bob, visibility="private")
    r = as_user(alice).post("/v1/tts", json={"text": "hello", "voice": "secret"})
    assert r.status_code == 404


def test_you_can_synthesise_with_a_shared_one(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("lent", bob, visibility="shared")
    r = as_user(alice).post("/v1/tts", json={"text": "hello", "voice": "lent"})
    assert r.status_code == 200, r.text


def test_the_openai_route_is_scoped_too(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("secret", bob, visibility="private")
    r = as_user(alice).post(
        "/v1/audio/speech", json={"model": "tts-1", "input": "hello", "voice": "secret"}
    )
    assert r.status_code == 404


def test_the_stream_route_is_scoped_too(seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("secret", bob, visibility="private")
    r = as_user(alice).post("/v1/tts/stream", json={"text": "hello", "voice": "secret"})
    assert r.status_code == 404


def test_presets_remain_available_to_everyone(seed_user, as_user):
    alice = seed_user("alice@example.com")
    r = as_user(alice).post("/v1/tts", json={"text": "hello", "voice": "narrator-us-f"})
    assert r.status_code == 200, r.text


# The ambiguity behaviour (409 without a disambiguator, the `owner` disambiguator itself, and an
# unknown owner email staying 404 rather than leaking as a 422) is shared logic in
# `voice_access`/`resolve.py`, reached identically from all three synthesis routes — the request
# body shape is the only thing that differs between them.
def _tts_body(voice: str, owner: str | None = None) -> dict:
    body = {"text": "hello", "voice": voice}
    if owner is not None:
        body["owner"] = owner
    return body


def _speech_body(voice: str, owner: str | None = None) -> dict:
    body = {"model": "tts-1", "input": "hello", "voice": voice}
    if owner is not None:
        body["owner"] = owner
    return body


_ROUTES = [
    ("/v1/tts", _tts_body),
    ("/v1/tts/stream", _tts_body),
    ("/v1/audio/speech", _speech_body),
]


@pytest.mark.parametrize("path,body", _ROUTES)
def test_an_ambiguous_shared_name_is_409_without_owner(path, body, seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    carol = seed_user("carol@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("narrator", bob, visibility="shared")
    seed_voice("narrator", carol, visibility="shared")
    r = as_user(alice).post(path, json=body("narrator"))
    assert r.status_code == 409, r.text


@pytest.mark.parametrize("path,body", _ROUTES)
def test_owner_disambiguates_a_shared_name(path, body, seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    carol = seed_user("carol@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("narrator", bob, visibility="shared")
    seed_voice("narrator", carol, visibility="shared")
    r = as_user(alice).post(path, json=body("narrator", owner="bob@example.com"))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path,body", _ROUTES)
def test_an_unknown_owner_email_is_404_not_422(path, body, seed_user, seed_voice, as_user):
    bob = seed_user("bob@example.com")
    alice = seed_user("alice@example.com")
    seed_voice("lent", bob, visibility="shared")
    r = as_user(alice).post(path, json=body("lent", owner="nobody@example.com"))
    assert r.status_code == 404
