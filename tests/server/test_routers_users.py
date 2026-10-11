# tests/server/test_routers_users.py
from __future__ import annotations

import threading

from auris.voice_library import keys, users

from .conftest import GOOD_PASSWORD, run_on_app_loop


def test_the_master_token_can_create_an_account_and_sees_the_password_once(client):
    r = client.post("/v1/users", json={"email": "alice@example.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "user"
    assert body["must_change_password"] is True
    assert len(body["password"]) >= 12

    # The generated password actually works, and forces a change.
    client.headers.pop("Authorization")
    login = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": body["password"]}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_a_listed_account_never_exposes_a_password_or_hash(client, seed_user):
    seed_user("alice@example.com")
    rows = client.get("/v1/users").json()
    assert [r["email"] for r in rows] == ["alice@example.com"]
    assert "password" not in rows[0]
    assert "password_hash" not in rows[0]


def test_a_duplicate_email_is_a_409(client, seed_user):
    seed_user("alice@example.com")
    r = client.post("/v1/users", json={"email": "Alice@Example.com"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_a_malformed_email_and_a_bad_role_are_422(client):
    assert client.post("/v1/users", json={"email": "nobody"}).status_code == 422
    r = client.post("/v1/users", json={"email": "a@example.com", "role": "superuser"})
    assert r.status_code == 422


def test_reset_issues_a_new_password_and_forces_a_change(client, seed_user):
    seed_user("alice@example.com", must_change_password=False)
    r = client.post("/v1/users/alice@example.com/reset")
    assert r.status_code == 200
    new_password = r.json()["password"]
    assert r.json()["must_change_password"] is True

    client.headers.pop("Authorization")
    assert (
        client.post(
            "/auth/login", json={"email": "alice@example.com", "password": GOOD_PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"email": "alice@example.com", "password": new_password}
        ).status_code
        == 200
    )


def test_disable_then_enable(client, seed_user):
    seed_user("alice@example.com")
    assert client.post("/v1/users/alice@example.com/disable").json()["disabled"] is True
    assert client.post("/v1/users/alice@example.com/enable").json()["disabled"] is False


def test_an_unknown_email_is_a_404(client):
    r = client.post("/v1/users/nobody@example.com/reset")
    assert r.status_code == 404
    # Not `voice_not_found`, which is what a bare LookupError would render as. See KeyNotFound's
    # docstring: this codebase gives each 404 surface its own code.
    assert r.json()["error"]["code"] == "user_not_found"
    assert client.post("/v1/users/nobody@example.com/disable").status_code == 404


def test_the_last_active_admin_cannot_be_disabled(client, seed_user):
    """Otherwise the only way back in is the master token, and an admin who has retired it is
    locked out of their own server."""
    seed_user("boss@example.com", role="admin")
    r = client.post("/v1/users/boss@example.com/disable")
    assert r.status_code == 409
    assert "last" in r.json()["error"]["message"].lower()


def test_a_second_admin_makes_the_first_disableable(client, seed_user):
    seed_user("boss@example.com", role="admin")
    seed_user("deputy@example.com", role="admin")
    assert client.post("/v1/users/boss@example.com/disable").status_code == 200


def test_two_concurrent_disables_cannot_remove_the_last_two_admins_over_http(client, seed_user):
    """Two real HTTP requests, fired from two threads released by the same barrier, against the
    real app — no injected sleeps. Exactly one must win; the guard must not be raceable."""
    seed_user("boss@example.com", role="admin")
    seed_user("deputy@example.com", role="admin")

    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _disable(email: str) -> None:
        barrier.wait()
        results[email] = client.post(f"/v1/users/{email}/disable").status_code

    threads = [
        threading.Thread(target=_disable, args=("boss@example.com",)),
        threading.Thread(target=_disable, args=("deputy@example.com",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == [200, 409], results
    assert run_on_app_loop(client, users.count_active_admins(client.app.state.db)) == 1


def test_a_non_admin_user_is_refused(seed_user, as_user):
    c = as_user(seed_user("alice@example.com"))
    assert c.get("/v1/users").status_code == 403
    assert c.post("/v1/users", json={"email": "x@example.com"}).status_code == 403


def test_an_admin_user_session_is_allowed(seed_user, as_user):
    boss = seed_user("boss@example.com", role="admin")
    assert as_user(boss).get("/v1/users").status_code == 200


def test_an_api_key_cannot_create_accounts_even_when_owned_by_an_admin(client, seed_user):
    boss = seed_user("boss@example.com", role="admin")
    _, plaintext = run_on_app_loop(
        client, keys.create_key(client.app.state.db, "ci-bot", owner_id=boss.id)
    )
    client.headers.update({"Authorization": f"Bearer {plaintext}"})
    r = client.post("/v1/users", json={"email": "sneaky@example.com"})
    assert r.status_code == 403


def test_the_users_api_is_not_advertised_in_the_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/v1/users")]


def test_the_users_router_declares_both_gates():
    """Structural, not behavioural, and deliberately so.

    Every current principal that `require_control_plane` would refuse is already refused by
    `require_admin` (a key's `admin` is always False), so no request can tell the two apart here.
    The second gate is defence-in-depth: if keys ever became able to inherit an owner's admin
    rights, it is what would still refuse them. Nothing else would catch its removal.
    """
    from auris.server.auth import require_admin, require_control_plane
    from auris.server.routers import users as users_router

    declared = [d.dependency for d in users_router.router.dependencies]
    assert require_admin in declared
    assert require_control_plane in declared
