from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auris.server import session
from auris.server.auth import KIND_USER, Principal, auth_dependency
from auris.server.deps import get_db, get_settings
from auris.server.errors import CurrentPasswordInvalid, Forbidden
from auris.voice_library import users

router = APIRouter()


class LoginRequest(BaseModel):
    # Bounded because this route is open and unauthenticated. (Starlette has already buffered
    # the body by this point — a hard request-size limit belongs at the reverse proxy.)
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


def _me(principal: Principal, cookie: str | None, role: str | None = None) -> dict:
    return {
        "principal": principal.name,
        "admin": principal.admin,
        "kind": principal.kind,
        "email": principal.name if principal.kind == KIND_USER else None,
        "role": role,
        "must_change_password": principal.must_change_password,
        "expires": session.expires_at(cookie) if cookie else None,
    }


def _set_cookie(response: JSONResponse, request: Request, value: str, hours: float) -> None:
    response.set_cookie(
        session.COOKIE,
        value,
        max_age=int(hours * 3600),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",  # tailnet deployments are plain http today
        path="/",
    )


@router.post("/auth/login")
async def login(
    body: LoginRequest, request: Request, conn=Depends(get_db), settings=Depends(get_settings)
) -> JSONResponse:
    """Exchange an email and password for an HttpOnly session cookie.

    Neither the master token nor an API key is accepted here: a cookie means a human, a bearer
    means a machine. The page never holds the password after this call.
    """
    user = await users.verify_login(conn, body.email, body.password)
    if user is None:
        # One message for "no such account", "wrong password" and "disabled". Unlike the old
        # key-based panel, naming the cause here would disclose which addresses are registered.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "email or password not accepted")
    value = session.issue(
        settings.auth_token,
        session.user_principal(user.id, user.password_version),
        settings.session_hours,
    )
    principal = Principal(user.email, user.is_admin, KIND_USER, user.id, user.must_change_password)
    response = JSONResponse(content=_me(principal, value, user.role))
    _set_cookie(response, request, value, settings.session_hours)
    return response


@router.post("/auth/password", dependencies=[Depends(auth_dependency)])
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    conn=Depends(get_db),
    settings=Depends(get_settings),
) -> JSONResponse:
    """Change your own password. Also the only route a must-change account may act on.

    Bumping `password_version` invalidates every existing cookie for this account, including the
    caller's, so a fresh one is issued in the response.
    """
    principal: Principal = request.state.principal
    if principal.kind != KIND_USER or principal.user_id is None:
        raise Forbidden("only a signed-in user account has a password to change")
    user = await users.get_user_by_id(conn, principal.user_id)
    if user is None:  # deleted between authentication and here
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this account is no longer active")
    if await users.verify_login(conn, user.email, body.current_password) is None:
        raise CurrentPasswordInvalid("current password not accepted")
    updated = await users.set_password(conn, user.id, body.new_password)  # ValueError => 422
    value = session.issue(
        settings.auth_token,
        session.user_principal(updated.id, updated.password_version),
        settings.session_hours,
    )
    fresh = Principal(updated.email, updated.is_admin, KIND_USER, updated.id, False)
    response = JSONResponse(content=_me(fresh, value, updated.role))
    _set_cookie(response, request, value, settings.session_hours)
    return response


@router.post("/auth/logout", dependencies=[Depends(auth_dependency)])
async def logout() -> JSONResponse:
    response = JSONResponse(content={"ok": True})
    # Same attributes as at set time. Browsers identify a cookie by name+domain+path, so the
    # rest is not needed to evict it, but matching avoids any proxy or client treating the
    # clearing cookie as a different, less safe one.
    response.delete_cookie(session.COOKIE, path="/", httponly=True, samesite="strict")
    return response


@router.get("/auth/me", dependencies=[Depends(auth_dependency)])
async def me(request: Request, conn=Depends(get_db)) -> dict:
    principal: Principal = request.state.principal
    role = None
    if principal.kind == KIND_USER and principal.user_id:
        user = await users.get_user_by_id(conn, principal.user_id)
        role = user.role if user else None
    return _me(principal, request.cookies.get(session.COOKIE), role)
