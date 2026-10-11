# tests/server/test_password_gate.py
from __future__ import annotations

import pytest

from .conftest import run_on_app_loop


@pytest.fixture
def pending(client, seed_user, as_user):
    """The TestClient as a signed-in user who has not yet changed their initial password."""
    return as_user(seed_user("new@example.com", must_change_password=True))


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/voices"),
        ("get", "/v1/whoami"),
        ("get", "/v1/info"),
        ("get", "/v1/keys"),
        ("get", "/v1/audio/models"),
        ("post", "/v1/tts"),
        ("post", "/v1/audio/speech"),
    ],
)
def test_every_real_route_is_refused_until_the_password_changes(pending, method, path):
    r = getattr(pending, method)(path)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "password_change_required"


@pytest.mark.parametrize("path", ["/auth/me", "/auth/logout", "/auth/password"])
def test_the_change_password_routes_stay_reachable(pending, path):
    """Otherwise the account is bricked: it cannot act, and cannot fix that.

    `not in (403, 404)` rather than `!= 403`: before the route exists the answer is 404, which
    would satisfy a bare `!= 403` and make this test pass without testing anything.
    """
    method = pending.get if path == "/auth/me" else pending.post
    r = method(path)
    assert r.status_code not in (403, 404), r.text


def test_the_gate_does_not_apply_to_a_user_who_has_changed_their_password(seed_user, as_user):
    settled = seed_user("settled@example.com", must_change_password=False)
    assert as_user(settled).get("/v1/voices").status_code == 200


def test_the_gate_does_not_apply_to_the_master_token_or_an_api_key(client):
    from auris.voice_library import keys

    assert client.get("/v1/voices").status_code == 200  # master bearer still set
    _, plaintext = run_on_app_loop(client, keys.create_key(client.app.state.db, "bot"))
    client.headers.update({"Authorization": f"Bearer {plaintext}"})
    assert client.get("/v1/voices").status_code == 200
