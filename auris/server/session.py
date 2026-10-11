"""Stateless, signed browser sessions.

Pure functions over strings — no FastAPI, no database, no server-side session store. A cookie
value is `v1|<expiry-unix>|<principal>|<hmac-sha256-hex>`; the principal is `u:<user_id>:
<password_version>` (see `user_principal`/`parse_user_principal`), so the caller is responsible
for re-loading that user and re-checking the version on every request (see
`auris.server.auth.resolve_session`). An API key can no longer open a session at all.

Sessions are stateless, so `/auth/logout` clears only the browser's copy: a cookie captured
off the wire stays valid until it expires (`AURIS_SESSION_HOURS`, 12 h by default). The escape
hatches are changing the account's password (bumps `password_version`, invalidating every
existing session for that user), disabling the account (re-checked on every request), and
rotating the master token, which invalidates every session at once.
"""

from __future__ import annotations

import hashlib
import hmac
import time

COOKIE = "auris_session"
CLIENT_HEADER = "X-Auris-Client"
CLIENT_VALUE = "dashboard"
_VERSION = "v1"


def _key(master: str) -> bytes:
    # Derived from the master token: no server-side state, sessions survive restarts, and
    # `token.sh rotate` invalidates every session at once.
    return hashlib.sha256(("auris-session:" + master).encode("utf-8")).digest()


def _sign(master: str, payload: str) -> str:
    return hmac.new(_key(master), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _usable(master: str) -> bool:
    """A blank master token is not a secret: `_key("")` is a constant anyone can derive, so a
    cookie signed with it would be forgeable by anyone. The server already refuses to start
    without AURIS_AUTH_TOKEN; this keeps the module safe on its own too."""
    return bool(master.strip())


def issue(master: str, principal: str, hours: float, now: float | None = None) -> str:
    """Build a cookie value: v1|<expiry-unix>|<principal>|<hmac>."""
    if not _usable(master):
        raise ValueError("cannot issue a session without a master token")
    expiry = int((now if now is not None else time.time()) + hours * 3600)
    payload = f"{_VERSION}|{expiry}|{principal}"
    return f"{payload}|{_sign(master, payload)}"


def verify(master: str, value: str, now: float | None = None) -> str | None:
    """Return the principal name if the value is well-formed, signed by us and unexpired.

    Total: any malformed, forged or expired value returns None rather than raising. The cookie
    is attacker-controlled, so raising here would turn a bad cookie into a 500 on every
    authenticated route."""
    if not _usable(master):
        return None
    parts = value.split("|")
    if len(parts) != 4 or parts[0] != _VERSION:
        return None
    payload = "|".join(parts[:3])
    # Compare as bytes: `hmac.compare_digest` REFUSES str arguments holding non-ASCII and raises
    # TypeError. Starlette latin-1-decodes header bytes, so a cookie with a high byte in the
    # signature field reaches here as non-ASCII str and would otherwise 500 the request.
    if not hmac.compare_digest(_sign(master, payload).encode("ascii"), parts[3].encode("utf-8")):
        return None
    expiry = expires_at(value)
    if expiry is None or (now if now is not None else time.time()) >= expiry:
        return None
    return parts[2]


def expires_at(value: str) -> int | None:
    """The expiry the value claims — unverified, so only for messages and the /auth/me body."""
    parts = value.split("|")
    if len(parts) != 4:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def is_expired(value: str, now: float | None = None) -> bool:
    """Whether the claimed expiry has passed. Used only to choose between the "expired" and
    "signed out" 401 message, never to grant access."""
    expiry = expires_at(value)
    return expiry is not None and (now if now is not None else time.time()) >= expiry


USER_PREFIX = "u:"


def user_principal(user_id: str, password_version: int) -> str:
    """The principal string for a signed-in user: `u:<user_id>:<password_version>`.

    The version travels in the cookie so `auth.resolve_session` can reject a session issued
    before a password change without any server-side session store.
    """
    if "|" in user_id or ":" in user_id:
        raise ValueError("user id must not contain the payload delimiter '|' or ':'")
    return f"{USER_PREFIX}{user_id}:{password_version}"


def parse_user_principal(principal: str | None) -> tuple[str, int] | None:
    """(user_id, password_version), or None if this is not a user principal.

    Total, like `verify`: a cookie minted before user accounts existed carries a key name or
    "admin" here, and must read as "not a user" rather than raising.
    """
    if not principal or not principal.startswith(USER_PREFIX):
        return None
    parts = principal[len(USER_PREFIX) :].split(":")
    if len(parts) != 2 or not parts[0]:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None
