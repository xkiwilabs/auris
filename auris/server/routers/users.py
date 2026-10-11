# auris/server/routers/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auris.server.auth import require_admin, require_control_plane
from auris.server.deps import get_db
from auris.voice_library import passwords, users
from auris.voice_library.exceptions import UserNotFound

# Admin-only *and* control-plane: an API key is refused even when its owner is an admin, so a
# leaked key cannot create accounts. `include_in_schema=False` because /openapi.json is
# unauthenticated and there is no reason to advertise credential management.
router = APIRouter(
    dependencies=[Depends(require_admin), Depends(require_control_plane)], include_in_schema=False
)


class UserCreate(BaseModel):
    email: str = Field(max_length=254)
    role: str = "user"


class UserInfo(BaseModel):
    email: str
    role: str
    must_change_password: bool
    disabled: bool
    created_at: str


class UserProvisioned(UserInfo):
    password: str  # generated, shown once, never stored in plaintext


def _info(u: users.User) -> UserInfo:
    return UserInfo(
        email=u.email,
        role=u.role,
        must_change_password=u.must_change_password,
        disabled=not u.is_active,
        created_at=u.created_at,
    )


async def _require_user(conn, email: str) -> users.User:
    user = await users.get_user_by_email(conn, email)
    if user is None:
        raise UserNotFound(f"no account for {email!r}")  # => 404 user_not_found
    return user


@router.get("/v1/users", response_model=list[UserInfo])
async def list_users(conn=Depends(get_db)):
    return [_info(u) for u in await users.list_users(conn)]


@router.post("/v1/users", status_code=201, response_model=UserProvisioned)
async def create_user(body: UserCreate, conn=Depends(get_db)):
    """Provision an account with a generated initial password, shown once.

    Generated rather than chosen by the admin: it removes the weakest link (one `Welcome123` for
    everyone) and keeps credentials out of shell history and CLI arguments.
    """
    password = passwords.generate_password()
    # ValueError => 422, NameTaken => 409, both via the installed handlers.
    user = await users.create_user(conn, body.email, password, role=body.role)
    return UserProvisioned(**_info(user).model_dump(), password=password)


@router.post("/v1/users/{email}/reset", response_model=UserProvisioned)
async def reset_password(email: str, conn=Depends(get_db)):
    """A forgotten-password reset. There is no email delivery, so an admin does this and hands
    the new password over out of band."""
    user = await _require_user(conn, email)
    password = passwords.generate_password()
    updated = await users.set_password(conn, user.id, password, must_change=True)
    return UserProvisioned(**_info(updated).model_dump(), password=password)


@router.post("/v1/users/{email}/disable", response_model=UserInfo)
async def disable_user(email: str, conn=Depends(get_db)):
    """Refusing to disable the last active admin is enforced inside the UPDATE (see
    users.set_disabled) — a preceding count could be raced by two concurrent requests."""
    user = await _require_user(conn, email)
    return _info(await users.set_disabled(conn, user.email, True, protect_last_admin=True))


@router.post("/v1/users/{email}/enable", response_model=UserInfo)
async def enable_user(email: str, conn=Depends(get_db)):
    user = await _require_user(conn, email)
    return _info(await users.set_disabled(conn, user.email, False))
