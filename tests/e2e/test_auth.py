"""Both principals, the key lifecycle, and the dashboard's cookie + CSRF contract."""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.server import SEEDED_ADMIN_EMAIL, SEEDED_KEY_NAME, SEEDED_USER_EMAIL, Instance

pytestmark = pytest.mark.e2e

DASH = {"X-Auris-Client": "dashboard"}  # the CSRF header every cookie request must carry


def test_no_bearer_and_bad_bearer_are_rejected(server: Instance):
    with httpx.Client(base_url=server.base_url, timeout=30.0) as c:
        assert c.get("/v1/voices").status_code == 401
        r = c.get("/v1/voices", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"


def test_a_named_key_authenticates_but_is_not_admin(as_key):
    assert as_key.get("/v1/voices").status_code == 200
    r = as_key.get("/v1/keys")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_issue_then_revoke_kills_the_key(admin, server: Instance):
    # The master token has no account of its own, so every key it mints must name one now.
    issued = admin.post("/v1/keys", json={"name": "throwaway", "owner_email": SEEDED_ADMIN_EMAIL})
    assert issued.status_code == 201
    key = issued.json()["key"]
    assert key.startswith("auris_")

    with server.client(key) as c:
        assert c.get("/v1/voices").status_code == 200

    revoked = admin.delete("/v1/keys/throwaway")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"]

    with server.client(key) as c:
        assert c.get("/v1/voices").status_code == 401

    names = {k["name"] for k in admin.get("/v1/keys").json()}
    assert {"throwaway", SEEDED_KEY_NAME} <= names


def test_duplicate_key_name_conflicts_and_bad_name_is_rejected(admin):
    owned = {"owner_email": SEEDED_ADMIN_EMAIL}
    assert admin.post("/v1/keys", json={"name": SEEDED_KEY_NAME, **owned}).status_code == 409

    # "admin" is 409, not 422: the name is taken — by the master token's principal, which must
    # stay distinguishable from a named key in every log line.
    reserved = admin.post("/v1/keys", json={"name": "admin", **owned})
    assert reserved.status_code == 409
    assert "reserved" in reserved.json()["error"]["message"]

    # A charset violation is a different thing: the request itself is invalid.
    bad = admin.post("/v1/keys", json={"name": "has spaces", **owned})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_request"
    assert admin.post("/v1/keys", json={"name": "a/b", **owned}).status_code == 422

    assert admin.delete("/v1/keys/never-existed").status_code == 404


def test_cookie_session_requires_the_csrf_header_and_dies_on_logout(server: Instance):
    with httpx.Client(base_url=server.base_url, timeout=30.0) as c:
        login = c.post(
            "/auth/login", json={"email": SEEDED_USER_EMAIL, "password": server.user_password}
        )
        assert login.status_code == 200
        body = login.json()
        assert body["principal"] == SEEDED_USER_EMAIL and body["admin"] is False
        assert body["expires"], "a cookie session must report when it expires"
        assert "auris_session" in c.cookies

        # The cookie alone is not enough: a custom header cannot be attached cross-origin
        # without a preflight, and this server never enables CORS.
        assert c.get("/auth/me").status_code == 401
        me = c.get("/auth/me", headers=DASH)
        assert me.status_code == 200 and me.json()["email"] == SEEDED_USER_EMAIL
        assert c.get("/v1/voices", headers=DASH).status_code == 200

        assert c.post("/auth/logout", headers=DASH).status_code == 200
        assert c.get("/auth/me", headers=DASH).status_code == 401
