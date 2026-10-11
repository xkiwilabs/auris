from __future__ import annotations

import hmac
from dataclasses import dataclass

import aiosqlite
import structlog
from fastapi import Header, HTTPException, Request, status

from auris.log import get_logger
from auris.server import session
from auris.server.errors import Forbidden, PasswordChangeRequired
from auris.voice_library import keys, users

_log = get_logger("auris.server.auth")

KIND_MASTER = "master"
KIND_KEY = "key"
KIND_USER = "user"


@dataclass(frozen=True)
class Principal:
    """Who is making the request.

    Three kinds. `master` is the shared token from AURIS_AUTH_TOKEN — break-glass admin, so a
    broken users table cannot lock an admin out. `key` is an API key: a machine credential,
    **never admin whoever owns it**, so a leaked key cannot extend its own foothold. `user` is a
    signed-in human holding a session cookie.
    """

    name: str
    admin: bool
    kind: str = KIND_MASTER
    user_id: str | None = None
    must_change_password: bool = False


ADMIN = Principal("admin", True, KIND_MASTER)

# Routes a principal with a pending password change may still reach. Everything else gets 403
# password_change_required, enforced in auth_dependency so no route can forget it.
_PASSWORD_CHANGE_ALLOWED = frozenset({"/auth/password", "/auth/me", "/auth/logout"})


async def resolve_bearer(token: str, master: str, conn: aiosqlite.Connection) -> Principal | None:
    """Master token → ADMIN; an unrevoked API key → its name (and bump last_used); else None.

    An owned key whose owner is gone or disabled is refused too: disabling an account is the
    action an admin takes to end someone's access, and a still-live API key would make that a
    false assurance. An unowned key (owner_id is None — a key issued before user accounts
    existed, per the migration) has no owner to be disabled, so it keeps working.
    """
    if master and hmac.compare_digest(token.encode("utf-8"), master.encode("utf-8")):
        return ADMIN
    if not token:
        return None
    record = await keys.lookup_active(conn, token)
    if record is None:
        return None
    if record.owner_id is not None:
        owner = await users.get_user_by_id(conn, record.owner_id)
        if owner is None or not owner.is_active:
            return None
    # Fire-and-forget, per the spec: this bookkeeping write sits on the path of every
    # authenticated request, and a transient DB error must not turn a valid credential into
    # a 500. Roll back so a half-open transaction can't strand the shared connection.
    try:
        await keys.touch_last_used(conn, record.id)
    except Exception as exc:  # noqa: BLE001 — auditing metadata, never worth failing a request
        _log.warning("could not record last_used_at for an API key", error=str(exc))
        try:
            await conn.rollback()
        except Exception:  # noqa: BLE001 — the connection is already unhealthy; nothing to add
            pass
    # admin=False unconditionally: see the Principal docstring.
    return Principal(record.name, False, KIND_KEY, record.owner_id)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail)


async def resolve_session(cookie: str, master: str, conn: aiosqlite.Connection) -> Principal:
    """Verify a session cookie and load the user it names.

    The user is re-read on every request, so disabling an account or changing its password ends
    its live sessions immediately — the cookie itself carries only an id and a version.
    """
    principal = session.verify(master, cookie)
    if principal is None:
        raise _unauthorized("session expired" if session.is_expired(cookie) else "signed out")
    parsed = session.parse_user_principal(principal)
    if parsed is None:
        # A cookie minted before user accounts existed (principal was a key name or "admin").
        # Signing in again is the only way forward; API keys are no longer session credentials.
        raise _unauthorized("signed out — please sign in again")
    user_id, version = parsed
    user = await users.get_user_by_id(conn, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("this account is no longer active")
    if user.password_version != version:
        raise _unauthorized("session expired — the password for this account changed")
    return Principal(user.email, user.is_admin, KIND_USER, user.id, user.must_change_password)


async def auth_dependency(
    request: Request,
    authorization: str | None = Header(default=None),
    x_auris_client: str | None = Header(default=None),
) -> Principal:
    """Authenticate the request and stamp `request.state.principal`.

    Two paths: `Authorization: Bearer <master-token | api-key>` (CLI, SDK), or the
    browser's `auris_session` cookie (also via `auris dashboard`'s proxy, which relays it) —
    which additionally needs the `X-Auris-Client: dashboard` header as CSRF protection (a custom header cannot be sent
    cross-origin without a CORS preflight, which this server never grants). The header is
    required on *every* cookie request, reads included, so no cookie route is left uncovered.
    A bearer request needs no such header: the browser will happily attach the cookie to it,
    but a cross-origin attacker cannot set `Authorization` either, for the same preflight
    reason — so the bearer path has no CSRF surface of its own."""
    settings = request.app.state.settings
    conn = request.app.state.db
    prefix = "Bearer "
    if authorization:
        if not authorization.startswith(prefix):
            raise _unauthorized("malformed Authorization header")
        principal = await resolve_bearer(authorization[len(prefix) :], settings.auth_token, conn)
        if principal is None:
            raise _unauthorized("invalid bearer token")
    else:
        cookie = request.cookies.get(session.COOKIE)
        if not cookie:
            # No credential of either kind. The wording keeps the old "missing bearer token"
            # substring for CLI users while naming the browser's path too.
            raise _unauthorized("missing bearer token or session cookie — signed out")
        if x_auris_client != session.CLIENT_VALUE:
            raise _unauthorized(f"missing {session.CLIENT_HEADER} header")
        principal = await resolve_session(cookie, settings.auth_token, conn)
    request.state.principal = principal
    # Clear first: per-request isolation of the context otherwise rests on the ASGI server
    # giving each request its own task context, which is its behaviour and not our invariant.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(principal=principal.name)  # every log line says who
    if principal.must_change_password and request.url.path not in _PASSWORD_CHANGE_ALLOWED:
        # Enforced here rather than per-router: auth_dependency is the one chokepoint every
        # authenticated route passes through, so a new route cannot forget this. FastAPI solves
        # router-level dependencies before validating the endpoint's own body, so this answers
        # 403 even for a route whose body is missing or malformed.
        raise PasswordChangeRequired(
            "this account must change its password before using the service"
        )
    return principal


async def require_admin(request: Request) -> Principal:
    """Use as a dependency on admin-only routes (after auth_dependency has run)."""
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        # Only reachable if this dependency is mounted ahead of auth_dependency; answer 401 so
        # the mounting mistake reads as "not authenticated", not "authenticated but refused".
        raise _unauthorized("missing bearer token")
    if not principal.admin:
        raise Forbidden("this action needs the admin (master) token")
    return principal


async def require_control_plane(request: Request) -> Principal:
    """Credential management (`/v1/keys`, `/v1/users`) — a user session or the master token.

    An API key is refused: a key that leaks can consume the service, but must not be able to
    mint further keys or create accounts.
    """
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        raise _unauthorized("missing bearer token")
    if principal.kind == KIND_KEY:
        raise Forbidden(
            "an API key cannot manage credentials — sign in to the dashboard, "
            "or use the master token"
        )
    return principal
