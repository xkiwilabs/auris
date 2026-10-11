from __future__ import annotations

from auris.server import session
from auris.voice_library import keys, users

from .conftest import run_on_app_loop

DASH = {"X-Auris-Client": "dashboard"}


def test_the_master_token_is_an_admin_master_principal(client):
    body = client.get("/v1/whoami").json()
    assert body == {
        "principal": "admin",
        "admin": True,
        "kind": "master",
        "user_id": None,
        "must_change_password": False,
    }


def test_an_api_key_is_never_admin_even_when_owned_by_an_admin(client, seed_user):
    """The migration assigns pre-existing keys to a human; inheriting the owner's role would
    silently promote every key on the box at upgrade time."""
    admin = seed_user("boss@example.com", role="admin")
    _, plaintext = run_on_app_loop(
        client, keys.create_key(client.app.state.db, "ci-bot", owner_id=admin.id)
    )
    body = client.get("/v1/whoami", headers={"Authorization": f"Bearer {plaintext}"}).json()
    assert body["kind"] == "key"
    assert body["admin"] is False
    assert body["user_id"] == admin.id


def test_a_session_cookie_resolves_to_its_user(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    body = as_user(alice).get("/v1/whoami").json()
    assert body["kind"] == "user"
    assert body["principal"] == "alice@example.com"
    assert body["user_id"] == alice.id
    assert body["admin"] is False


def test_an_admin_users_session_is_admin(client, seed_user, as_user):
    boss = seed_user("boss@example.com", role="admin")
    assert as_user(boss).get("/v1/whoami").json()["admin"] is True
    assert as_user(boss).get("/v1/whoami/admin").status_code == 200


def test_a_stale_password_version_is_rejected(client, seed_user, as_user):
    """A password change must end every live session for that user."""
    alice = seed_user("alice@example.com")
    as_user(alice)  # cookie minted at the current version
    run_on_app_loop(client, users.set_password(client.app.state.db, alice.id, "new-long-password"))
    assert client.get("/v1/whoami").status_code == 401


def test_a_disabled_users_session_is_rejected(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    as_user(alice)
    run_on_app_loop(client, users.set_disabled(client.app.state.db, "alice@example.com", True))
    assert client.get("/v1/whoami").status_code == 401


def test_a_deleted_users_session_is_rejected(client, as_anon):
    c = as_anon()
    c.headers.update(DASH)
    c.cookies.set(
        session.COOKIE, session.issue("secret", session.user_principal("nope", 1), hours=1)
    )
    assert c.get("/v1/whoami").status_code == 401


def test_a_pre_accounts_cookie_no_longer_authenticates(client, as_anon):
    """Cookies minted before this change carry a key name or "admin" as the principal. They must
    read as signed out, not resolve to anything."""
    for principal in ("admin", "ci-bot"):
        c = as_anon()
        c.headers.update(DASH)
        c.cookies.set(session.COOKIE, session.issue("secret", principal, hours=1))
        assert c.get("/v1/whoami").status_code == 401, principal


def test_the_control_plane_refuses_an_api_key_but_allows_master_and_user_sessions(
    client, seed_user, as_user
):
    alice = seed_user("alice@example.com")
    _, plaintext = run_on_app_loop(client, keys.create_key(client.app.state.db, "ci-bot"))

    assert client.get("/v1/whoami/control-plane").status_code == 200  # master bearer

    client.headers.update({"Authorization": f"Bearer {plaintext}"})
    r = client.get("/v1/whoami/control-plane")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    assert as_user(alice).get("/v1/whoami/control-plane").status_code == 200


def test_a_cookie_still_needs_the_csrf_header(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    as_user(alice).headers.pop(session.CLIENT_HEADER)
    r = client.get("/v1/whoami")
    assert r.status_code == 401
    assert "X-Auris-Client" in r.json()["error"]["message"]


def test_a_key_owned_by_a_disabled_user_stops_working(client, seed_user):
    """Disabling an account is the action an admin takes to end someone's access; a still-live
    API key of theirs would make that a false assurance."""
    alice = seed_user("alice@example.com")
    _, plaintext = run_on_app_loop(
        client, keys.create_key(client.app.state.db, "alices-bot", owner_id=alice.id)
    )
    assert (
        client.get("/v1/whoami", headers={"Authorization": f"Bearer {plaintext}"}).status_code
        == 200
    )
    run_on_app_loop(client, users.set_disabled(client.app.state.db, "alice@example.com", True))
    r = client.get("/v1/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 401


def test_a_key_owned_by_an_active_user_keeps_working(client, seed_user):
    alice = seed_user("alice@example.com")
    _, plaintext = run_on_app_loop(
        client, keys.create_key(client.app.state.db, "alices-bot", owner_id=alice.id)
    )
    r = client.get("/v1/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200
    assert r.json()["kind"] == "key"


def test_an_unowned_key_keeps_working(client):
    """A key issued before user accounts existed has no owner to be disabled — the migration
    deliberately left it unowned, and it must not be treated as if its (nonexistent) owner were
    disabled."""
    _, plaintext = run_on_app_loop(client, keys.create_key(client.app.state.db, "legacy-bot"))
    r = client.get("/v1/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200
    assert r.json()["user_id"] is None
