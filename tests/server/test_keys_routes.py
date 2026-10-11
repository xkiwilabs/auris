from __future__ import annotations

from auris.voice_library import keys as keylib

from .conftest import run_on_app_loop

KEY_INFO_FIELDS = {"name", "created_at", "revoked_at", "last_used_at", "owner"}


def test_issue_list_revoke_roundtrip(client, seed_user):
    owner = seed_user("alice@example.com")
    r = client.post("/v1/keys", json={"name": "alice", "owner_email": owner.email})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "alice" and body["key"].startswith("auris_")

    listed = client.get("/v1/keys").json()
    assert [k["name"] for k in listed] == ["alice"]
    assert "key" not in listed[0] and "key_hash" not in listed[0]

    # the issued key authenticates as a non-admin principal
    r = client.get("/v1/voices", headers={"Authorization": "Bearer " + body["key"]})
    assert r.status_code == 200

    r = client.delete("/v1/keys/alice")
    assert r.status_code == 200 and r.json()["revoked_at"]
    r = client.get("/v1/voices", headers={"Authorization": "Bearer " + body["key"]})
    assert r.status_code == 401


def test_issuing_a_taken_name_is_409(client, seed_user):
    owner = seed_user("alice@example.com")
    assert (
        client.post("/v1/keys", json={"name": "bob", "owner_email": owner.email}).status_code == 201
    )
    r = client.post("/v1/keys", json={"name": "Bob", "owner_email": owner.email})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_issuing_the_reserved_admin_name_is_409(client, seed_user):
    owner = seed_user("alice@example.com")
    # "admin" is the master token's principal, so a key may never take it (see keys.py).
    r = client.post("/v1/keys", json={"name": "admin", "owner_email": owner.email})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"


def test_revoking_unknown_is_404(client):
    r = client.delete("/v1/keys/nobody")
    assert r.status_code == 404, r.text
    # Assert the body too: a missing/unmounted route would also 404, which would let this test
    # pass while the keys router was not wired up at all.
    err = r.json()["error"]
    assert err["code"] == "key_not_found" and "nobody" in err["message"]


def test_blank_name_is_422(client, seed_user):
    owner = seed_user("alice@example.com")
    r = client.post("/v1/keys", json={"name": "  ", "owner_email": owner.email})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_a_revoked_name_cannot_be_reissued(client, seed_user):
    owner = seed_user("alice@example.com")
    assert (
        client.post("/v1/keys", json={"name": "erin", "owner_email": owner.email}).status_code
        == 201
    )
    assert client.delete("/v1/keys/erin").status_code == 200
    # The row survives revocation so the audit trail stays unambiguous, which means the name
    # stays taken. Re-issuing it would make old log lines refer to two different credentials.
    r = client.post("/v1/keys", json={"name": "erin", "owner_email": owner.email})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_a_name_that_could_not_be_revoked_over_http_is_refused(client, seed_user):
    owner = seed_user("alice@example.com")
    # A "/" in the name would route DELETE /v1/keys/{name} nowhere (Starlette percent-decodes
    # before matching, so %2F misses too) — the key would be live and unrevokable for ever.
    for name in ["slash/name", "with space", "dots/../..", "nl\nline", "x" * 65]:
        r = client.post("/v1/keys", json={"name": name, "owner_email": owner.email})
        assert r.status_code == 422, f"{name!r} was accepted: {r.text}"
        assert r.json()["error"]["code"] == "invalid_request"
    assert client.get("/v1/keys").json() == []


def test_listed_keys_expose_exactly_the_metadata_fields(client, seed_user):
    owner = seed_user("alice@example.com")
    assert (
        client.post("/v1/keys", json={"name": "dave", "owner_email": owner.email}).status_code
        == 201
    )
    listed = client.get("/v1/keys").json()
    # The secret is shown once on issue and never again — and the hash never leaves the DB.
    assert set(listed[0]) == KEY_INFO_FIELDS


def test_key_principal_cannot_manage_keys(client, seed_user):
    owner = seed_user("alice@example.com")
    key = client.post("/v1/keys", json={"name": "carol", "owner_email": owner.email}).json()["key"]
    h = {"Authorization": "Bearer " + key}
    r = client.get("/v1/keys", headers=h)
    assert r.status_code == 403
    # 403 with code "forbidden", not 401: the key really did authenticate and was then refused by
    # require_control_plane — so this pins the machine-credential gate, not a broken credential.
    # An API key is refused here even when its owner is an admin.
    assert r.json()["error"]["code"] == "forbidden"
    assert client.post("/v1/keys", json={"name": "x"}, headers=h).status_code == 403
    assert client.delete("/v1/keys/carol", headers=h).status_code == 403


def test_keys_routes_need_auth(client):
    for call in (
        lambda h: client.get("/v1/keys", headers=h),
        lambda h: client.post("/v1/keys", json={"name": "frank"}, headers=h),
        lambda h: client.delete("/v1/keys/frank", headers=h),
    ):
        assert call({"Authorization": ""}).status_code == 401
        assert call({"Authorization": "Bearer wrong"}).status_code == 401


def test_the_key_surface_is_not_advertised_in_the_public_schema(client):
    # /openapi.json and /docs are unauthenticated, so the credential-management paths and the
    # KeyIssued schema stay out of them.
    schema = client.get("/openapi.json").json()
    assert not [p for p in schema["paths"] if p.startswith("/v1/keys")]
    assert not [s for s in schema.get("components", {}).get("schemas", {}) if s.startswith("Key")]


def _issue(client, name, owner_id=None):
    return run_on_app_loop(client, keylib.create_key(client.app.state.db, name, owner_id))


def test_a_user_sees_only_their_own_keys(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    bob = seed_user("bob@example.com")
    _issue(client, "alice-bot", alice.id)
    _issue(client, "bob-bot", bob.id)
    _issue(client, "legacy-bot", None)

    rows = as_user(alice).get("/v1/keys").json()
    assert [r["name"] for r in rows] == ["alice-bot"]


def test_an_admin_sees_every_key_with_its_owner(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    boss = seed_user("boss@example.com", role="admin")
    _issue(client, "alice-bot", alice.id)
    _issue(client, "legacy-bot", None)

    rows = as_user(boss).get("/v1/keys").json()
    assert {r["name"]: r["owner"] for r in rows} == {
        "alice-bot": "alice@example.com",
        "legacy-bot": None,
    }


def test_a_key_a_user_issues_is_owned_by_them(seed_user, as_user):
    alice = seed_user("alice@example.com")
    r = as_user(alice).post("/v1/keys", json={"name": "my-laptop"})
    assert r.status_code == 201
    assert r.json()["owner"] == "alice@example.com"
    assert r.json()["key"].startswith("auris_")


def test_a_user_cannot_revoke_someone_elses_key(client, seed_user, as_user):
    alice = seed_user("alice@example.com")
    bob = seed_user("bob@example.com")
    _issue(client, "bob-bot", bob.id)
    # 404, not 403: the same answer as a key that does not exist, so the endpoint is not a
    # directory of other people's key names.
    assert as_user(alice).delete("/v1/keys/bob-bot").status_code == 404


def test_an_admin_can_revoke_anyones_key(client, seed_user, as_user):
    bob = seed_user("bob@example.com")
    boss = seed_user("boss@example.com", role="admin")
    _issue(client, "bob-bot", bob.id)
    r = as_user(boss).delete("/v1/keys/bob-bot")
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None


def test_the_master_token_must_name_an_owner(client):
    """The master token has no account of its own to fall back to — unlike a signed-in human,
    it cannot default to "the key is mine", because a key it minted that way would be
    unowned and therefore unable to create or modify any voice (see voice_access)."""
    r = client.post("/v1/keys", json={"name": "ci-bot"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request"
    assert "owner_email" in body["message"]


def test_the_master_token_naming_an_unknown_address_is_404(client):
    r = client.post("/v1/keys", json={"name": "ci-bot", "owner_email": "ghost@example.com"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


def test_the_master_token_naming_a_real_address_mints_an_owned_key(client, seed_user):
    owner = seed_user("alice@example.com")
    r = client.post("/v1/keys", json={"name": "ci-bot", "owner_email": owner.email})
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == owner.email


def test_a_non_admin_user_naming_someone_else_is_403(seed_user, as_user):
    alice = seed_user("alice@example.com")
    bob = seed_user("bob@example.com")
    r = as_user(alice).post("/v1/keys", json={"name": "sneaky", "owner_email": bob.email})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_a_non_admin_user_naming_themself_explicitly_still_works(seed_user, as_user):
    alice = seed_user("alice@example.com")
    r = as_user(alice).post("/v1/keys", json={"name": "my-own", "owner_email": alice.email})
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == alice.email


def test_a_non_admin_user_naming_themself_in_a_different_case_still_works(seed_user, as_user):
    """`validate_email` only strips the address; it does not casefold it, so the comparison
    must be case-insensitive or a caller typing their own address differently than it was
    created would be wrongly refused."""
    alice = seed_user("alice@example.com")
    r = as_user(alice).post("/v1/keys", json={"name": "my-own", "owner_email": "Alice@Example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == alice.email


def test_a_non_admin_naming_an_unregistered_address_is_403_not_404(seed_user, as_user):
    """The order matters: a non-admin's identity must be decided before any lookup of the
    target address, or a real-other-account-403 versus unregistered-address-404 split becomes a
    free account-existence oracle for any signed-in user — the exact disclosure sign-in's dummy
    hash exists to deny."""
    alice = seed_user("alice@example.com")
    r = as_user(alice).post("/v1/keys", json={"name": "sneaky", "owner_email": "ghost@example.com"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_an_admin_naming_the_same_unregistered_address_is_still_404(client):
    """The asymmetry is the whole point: an admin may already list every account, so a
    truthful 404 for them discloses nothing a non-admin's 403 does not already hide."""
    r = client.post("/v1/keys", json={"name": "ci-bot", "owner_email": "ghost@example.com"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


def test_an_admin_user_may_name_someone_else(seed_user, as_user):
    boss = seed_user("boss@example.com", role="admin")
    alice = seed_user("alice@example.com")
    r = as_user(boss).post("/v1/keys", json={"name": "for-alice", "owner_email": alice.email})
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == alice.email


def test_minting_for_a_disabled_account_is_refused(client, seed_user):
    from auris.voice_library import users as users_lib

    from .conftest import run_on_app_loop

    alice = seed_user("alice@example.com")
    run_on_app_loop(client, users_lib.set_disabled(client.app.state.db, alice.email, True))
    r = client.post("/v1/keys", json={"name": "ci-bot", "owner_email": alice.email})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert "disabled" in r.json()["error"]["message"]


def test_an_api_key_cannot_mint_another_key(client, seed_user):
    alice = seed_user("alice@example.com")
    _, plaintext = _issue(client, "alice-bot", alice.id)
    client.headers.update({"Authorization": f"Bearer {plaintext}"})
    assert client.post("/v1/keys", json={"name": "sneaky"}).status_code == 403
    assert client.get("/v1/keys").status_code == 403
