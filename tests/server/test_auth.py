from __future__ import annotations

import pytest
import pytest_asyncio

from auris.server import auth
from auris.voice_library import db, keys


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_master_token_is_admin(conn):
    p = await auth.resolve_bearer("master", "master", conn)
    assert p == auth.ADMIN and p.admin is True


@pytest.mark.asyncio
async def test_api_key_resolves_to_its_name(conn):
    _, plaintext = await keys.create_key(conn, "alice")
    p = await auth.resolve_bearer(plaintext, "master", conn)
    assert p == auth.Principal("alice", False, auth.KIND_KEY, None)


@pytest.mark.asyncio
async def test_api_key_bumps_last_used(conn):
    _, plaintext = await keys.create_key(conn, "alice")
    await auth.resolve_bearer(plaintext, "master", conn)
    assert (await keys.get_key_by_name(conn, "alice")).last_used_at is not None


@pytest.mark.asyncio
async def test_revoked_key_does_not_resolve(conn):
    _, plaintext = await keys.create_key(conn, "alice")
    await keys.revoke_key(conn, "alice")
    assert await auth.resolve_bearer(plaintext, "master", conn) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "wrong", "auris_nope", "master "])
async def test_bad_bearers_do_not_resolve(conn, token):
    assert await auth.resolve_bearer(token, "master", conn) is None


def test_principal_is_stamped_on_the_request(client):
    # the shared `client` fixture authenticates with the master token
    r = client.get("/v1/whoami")
    assert r.status_code == 200
    assert r.json() == {
        "principal": "admin",
        "admin": True,
        "kind": "master",
        "user_id": None,
        "must_change_password": False,
    }


def test_admin_route_rejects_a_key_principal(client):
    from tests.server.conftest import run_on_app_loop

    async def _mk():
        _, plaintext = await keys.create_key(client.app.state.db, "alice")
        return plaintext

    key = run_on_app_loop(client, _mk())
    r = client.get("/v1/whoami/admin", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    assert client.get("/v1/whoami/admin").status_code == 200  # master token passes


@pytest.mark.parametrize(
    "header", ["", "secret", "Basic secret", "Bearer wrong", "Bearer ", "Bearer"]
)
def test_authed_route_rejects_a_bad_authorization_header(client, header):
    # The old header-only helper is gone; 401 on a missing/garbled header must not regress.
    r = client.get("/v1/voices", headers={"Authorization": header})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_a_failed_last_used_write_does_not_reject_a_valid_key(conn, monkeypatch):
    """The bump is bookkeeping: a transient write error must not 500 a valid credential."""
    import sqlite3

    _, plaintext = await keys.create_key(conn, "alice")

    async def boom(_conn, _key_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(keys, "touch_last_used", boom)
    resolved = await auth.resolve_bearer(plaintext, "master", conn)
    assert resolved == auth.Principal("alice", False, auth.KIND_KEY, None)
    assert conn._conn.in_transaction is False


def test_require_admin_ahead_of_auth_answers_401_not_403(client):
    """Guards the mounting order: require_admin before auth_dependency must read as 401."""
    from fastapi import APIRouter, Depends

    from auris.server.auth import auth_dependency, require_admin

    inverted = APIRouter(dependencies=[Depends(auth_dependency)])

    @inverted.get("/v1/inverted-probe")
    async def _probe():
        return {"ok": True}

    # require_admin is mounted OUTSIDE, so it resolves before auth_dependency.
    client.app.include_router(inverted, dependencies=[Depends(require_admin)])
    r = client.get("/v1/inverted-probe", headers={"Authorization": ""})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
