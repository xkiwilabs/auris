from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from auris.server.auth import Principal, require_control_plane
from auris.server.deps import get_db
from auris.server.errors import Forbidden
from auris.voice_library import keys, users
from auris.voice_library.exceptions import KeyNotFound, UserNotFound

# Any authenticated *human* (or the master token) may manage their own keys; an API key may not
# (require_control_plane). `include_in_schema=False`: /openapi.json is unauthenticated, and
# there is no reason to advertise the credential-management surface.
router = APIRouter(dependencies=[Depends(require_control_plane)], include_in_schema=False)


class KeyCreate(BaseModel):
    name: str
    # The account that will own the issued key. Optional for a principal that already carries
    # one (it then defaults to themselves); required for the master token, which has none.
    owner_email: str | None = None


class KeyInfo(BaseModel):
    name: str
    created_at: str
    revoked_at: str | None
    last_used_at: str | None
    owner: str | None  # the owner's email; None for keys issued before accounts, or by the master


class KeyIssued(KeyInfo):
    key: str  # shown once; never stored, never listed


async def _emails_by_id(conn) -> dict[str, str]:
    return {u.id: u.email for u in await users.list_users(conn)}


def _info(k: keys.ApiKey, emails: dict[str, str]) -> KeyInfo:
    return KeyInfo(
        name=k.name,
        created_at=k.created_at,
        revoked_at=k.revoked_at,
        last_used_at=k.last_used_at,
        owner=emails.get(k.owner_id) if k.owner_id else None,
    )


def _principal(request: Request) -> Principal:
    return request.state.principal


@router.get("/v1/keys", response_model=list[KeyInfo])
async def list_keys(request: Request, conn=Depends(get_db)):
    """Your own keys — or every key, with its owner, for an admin."""
    p = _principal(request)
    owner_filter = None if p.admin else p.user_id
    emails = await _emails_by_id(conn)
    return [_info(k, emails) for k in await keys.list_keys(conn, owner_filter)]


async def _resolve_key_owner(conn, p: Principal, owner_email: str | None) -> str:
    """The user id that will own the new key.

    A principal with no account of its own (the master token) must name one explicitly — there
    is nothing to default to, and an ownerless key can never create or modify a voice (see
    `voice_access.find_for_clone`). A principal with an account defaults to naming itself;
    naming a *different* account is an admin-only act, refused for a non-admin.

    A non-admin's identity is decided *before* any lookup of the target address — compared
    against the caller's own, already-authenticated account, never by looking the address up
    and checking permission afterwards. That ordering matters: looking up first would answer
    403 for a real other account and 404 for an unregistered one, and that difference is a free
    account-existence oracle for any signed-in user — exactly the disclosure `users.verify_login`
    runs a dummy hash to deny at sign-in, and the rule `routers/voices.py::_owner_id_for` already
    follows for `?owner=`. A non-admin now gets 403 for *any* address that is not their own,
    existing or not; an admin still gets a truthful 404, which discloses nothing a non-admin's
    own path would not already hide, since an admin may list every account anyway.
    """
    if owner_email is None:
        if p.user_id is not None:
            return p.user_id
        raise ValueError(
            "owner_email is required: the master token has no account of its own, so a key "
            "it issues must name one — see POST /v1/users to create one first"
        )
    email = users.validate_email(owner_email)
    if not p.admin:
        # `validate_email` only strips the address; it does not casefold it (confirmed by
        # reading `create_user`, which stores exactly what `validate_email` returns), so a
        # stored email can differ from a normalised input by case even though the DB's own
        # lookups are case-insensitive (`COLLATE NOCASE`). Compare case-insensitively here too,
        # or a non-admin naming their own address in a different case would be wrongly refused.
        me = await users.get_user_by_id(conn, p.user_id) if p.user_id else None
        if me is None or me.email.casefold() != email.casefold():
            raise Forbidden("you may only issue a key owned by your own account")
        return me.id
    owner = await users.get_user_by_email(conn, email)
    if owner is None:
        # Mirrors routers/users.py's own `UserNotFound` for an unknown email (reset/disable/
        # enable).
        raise UserNotFound(f"no account for {email!r}")
    if not owner.is_active:
        raise ValueError(
            f"the account for {email!r} is disabled; enable it before issuing a key for it"
        )
    return owner.id


@router.post("/v1/keys", status_code=201, response_model=KeyIssued)
async def issue_key(body: KeyCreate, request: Request, conn=Depends(get_db)):
    """Issue a key. Owned by the caller by default; `owner_email` may name a different
    account, but only for an admin (the master token or an admin user) — the master token must
    always supply it, since it has no account of its own to fall back to."""
    p = _principal(request)
    owner_id = await _resolve_key_owner(conn, p, body.owner_email)
    # ValueError => 422, NameTaken => 409, via the installed handlers.
    record, plaintext = await keys.create_key(conn, body.name, owner_id=owner_id)
    emails = await _emails_by_id(conn)
    return KeyIssued(**_info(record, emails).model_dump(), key=plaintext)


@router.delete("/v1/keys/{name}", response_model=KeyInfo)
async def revoke_key(name: str, request: Request, conn=Depends(get_db)):
    """Revoke one of your own keys; an admin may revoke any.

    Someone else's key answers 404, the same as a name that does not exist, so this endpoint is
    not a directory of other people's key names.
    """
    p = _principal(request)
    existing = await keys.get_key_by_name(conn, name)
    if existing is None or (not p.admin and existing.owner_id != p.user_id):
        raise KeyNotFound(f"API key {name!r} not found")  # => 404 key_not_found
    record = await keys.revoke_key(conn, name)
    if record is None:  # revoked between the read and the write
        raise KeyNotFound(f"API key {name!r} not found")
    emails = await _emails_by_id(conn)
    return _info(record, emails)
