# tests/e2e/test_accounts.py
"""Accounts, over HTTP, against a real server."""

from __future__ import annotations

import contextlib

import httpx
import pytest

from tests.e2e.server import (
    SEEDED_ADMIN_EMAIL,
    SEEDED_USER_EMAIL,
    Instance,
)

pytestmark = pytest.mark.e2e

DASH = {"X-Auris-Client": "dashboard"}


def _login(server: Instance, email: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=server.base_url, timeout=60.0, headers=DASH)
    r = c.post("/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return c  # the cookie is now in the client's jar


def test_a_user_signs_in_and_can_synthesise(server: Instance):
    with contextlib.closing(_login(server, SEEDED_USER_EMAIL, server.user_password)) as c:
        me = c.get("/auth/me").json()
        assert me["email"] == SEEDED_USER_EMAIL
        assert me["role"] == "user"
        assert me["must_change_password"] is False
        r = c.post("/v1/tts", json={"voice": "narrator-uk-f", "text": "Hello from auris."})
        assert r.status_code == 200


def test_a_wrong_password_is_refused_identically_to_an_unknown_account(server: Instance):
    with httpx.Client(base_url=server.base_url, timeout=30.0) as c:
        wrong = c.post(
            "/auth/login", json={"email": SEEDED_USER_EMAIL, "password": "nope-nope-nope"}
        )
        unknown = c.post(
            "/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-nope"}
        )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_neither_the_master_token_nor_an_api_key_opens_a_session(server: Instance):
    with httpx.Client(base_url=server.base_url, timeout=30.0) as c:
        assert c.post(
            "/auth/login", json={"email": "admin", "password": server.token}
        ).status_code in (401, 422)
        assert c.post("/auth/login", json={"email": "bot", "password": server.key}).status_code in (
            401,
            422,
        )


def test_a_new_account_must_change_its_password_before_doing_anything(server: Instance):
    with server.client() as admin:  # master-token bearer
        provisioned = admin.post("/v1/users", json={"email": "fresh@example.com"}).json()

    with httpx.Client(base_url=server.base_url, timeout=60.0, headers=DASH) as c:
        login = c.post(
            "/auth/login", json={"email": "fresh@example.com", "password": provisioned["password"]}
        )
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True

        blocked = c.get("/v1/voices")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "password_change_required"

        changed = c.post(
            "/auth/password",
            json={
                "current_password": provisioned["password"],
                "new_password": "a-fresh-long-password",
            },
        )
        assert changed.status_code == 200
        assert c.get("/v1/voices").status_code == 200


def test_changing_a_password_ends_other_live_sessions(server: Instance):
    with server.client() as admin:
        provisioned = admin.post("/v1/users", json={"email": "twosessions@example.com"}).json()
    first = _login(server, "twosessions@example.com", provisioned["password"])
    second = _login(server, "twosessions@example.com", provisioned["password"])
    try:
        first.post(
            "/auth/password",
            json={
                "current_password": provisioned["password"],
                "new_password": "changed-it-elsewhere",
            },
        ).raise_for_status()
        # The other browser's cookie is now stale.
        assert second.get("/auth/me").status_code == 401
    finally:
        first.close()
        second.close()


def test_a_user_issues_and_rotates_their_own_key_and_it_works_as_a_bearer(server: Instance):
    with contextlib.closing(_login(server, SEEDED_USER_EMAIL, server.user_password)) as c:
        issued = c.post("/v1/keys", json={"name": "e2e-rotate-me"})
        assert issued.status_code == 201
        plaintext = issued.json()["key"]
        assert issued.json()["owner"] == SEEDED_USER_EMAIL

        with httpx.Client(
            base_url=server.base_url, headers={"Authorization": f"Bearer {plaintext}"}, timeout=60.0
        ) as bot:
            assert bot.get("/v1/voices").status_code == 200
            # A key is a machine credential: it cannot manage credentials or open a session.
            assert bot.get("/v1/keys").status_code == 403
            assert bot.post("/v1/users", json={"email": "sneaky@example.com"}).status_code == 403

        assert c.delete("/v1/keys/e2e-rotate-me").status_code == 200
        with httpx.Client(
            base_url=server.base_url, headers={"Authorization": f"Bearer {plaintext}"}, timeout=60.0
        ) as bot:
            assert bot.get("/v1/voices").status_code == 401


def test_a_user_cannot_see_or_revoke_another_users_key(server: Instance):
    with contextlib.closing(_login(server, SEEDED_ADMIN_EMAIL, server.admin_password)) as boss:
        boss.post("/v1/keys", json={"name": "e2e-boss-key"}).raise_for_status()
    with contextlib.closing(_login(server, SEEDED_USER_EMAIL, server.user_password)) as c:
        names = [k["name"] for k in c.get("/v1/keys").json()]
        assert "e2e-boss-key" not in names
        assert c.delete("/v1/keys/e2e-boss-key").status_code == 404


def test_only_an_admin_reaches_the_users_api(server: Instance):
    with contextlib.closing(_login(server, SEEDED_USER_EMAIL, server.user_password)) as c:
        assert c.get("/v1/users").status_code == 403
    with contextlib.closing(_login(server, SEEDED_ADMIN_EMAIL, server.admin_password)) as boss:
        emails = [u["email"] for u in boss.get("/v1/users").json()]
        assert SEEDED_USER_EMAIL in emails and SEEDED_ADMIN_EMAIL in emails


def test_a_disabled_account_loses_access_immediately(server: Instance):
    with server.client() as admin:
        provisioned = admin.post("/v1/users", json={"email": "tobedisabled@example.com"}).json()
    c = _login(server, "tobedisabled@example.com", provisioned["password"])
    try:
        # Still pending a change, so /auth/me is the route to probe.
        assert c.get("/auth/me").status_code == 200
        with server.client() as admin:
            admin.post("/v1/users/tobedisabled@example.com/disable").raise_for_status()
        assert c.get("/auth/me").status_code == 401
    finally:
        c.close()


def test_the_master_token_still_works_as_break_glass(server: Instance):
    """A broken users table must never lock an admin out of their own server."""
    with server.client() as admin:
        assert admin.get("/v1/voices").status_code == 200
        assert admin.get("/v1/users").status_code == 200
