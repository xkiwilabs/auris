"""The `users` table: named human accounts for the dashboard.

Shaped like `keys.py` on purpose — same rollback discipline, same NameTaken/ValueError vocabulary,
so the error envelope at the HTTP edge needs no new cases. Import-light: the CLI imports this.
"""

from __future__ import annotations

import asyncio
import functools
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from auris.voice_library import passwords
from auris.voice_library.exceptions import LastActiveAdmin, NameTaken

ADMIN_ROLE = "admin"
USER_ROLE = "user"
ROLES = frozenset({ADMIN_ROLE, USER_ROLE})

# Deliberately narrow, not RFC 5322. An email is a path segment in
# `POST /v1/users/{email}/reset` and a field in every audit log line: "/" would mint an account
# that could never be reset over HTTP, and whitespace or control characters could forge log
# lines. 254 is the practical maximum length of an address.
_EMAIL_RE = re.compile(r"[^@\s/]{1,64}@[^@\s/.]+(\.[^@\s/.]+)+")
_MAX_EMAIL = 254

_COLUMNS = "id, email, role, password_version, must_change_password, created_at, disabled_at"  # never password_hash


@dataclass
class User:
    id: str
    email: str
    role: str
    password_version: int
    must_change_password: bool
    created_at: str
    disabled_at: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@functools.cache
def _dummy_hash() -> str:
    """One cached hash for the no-such-account branch to verify against.

    Cached, and computed lazily: hashing per call costs TWO derivations against the real
    path's one, which is itself an enumeration oracle; doing it at import time would add
    ~48 ms to every CLI command. Only the first unknown-email request in a process pays
    the extra derivation, which is far too rare to be a signal.
    """
    return passwords.hash_password("timing-equaliser-value")


def _verify_dummy(password: str) -> bool:
    """The no-such-account branch's single derivation.

    A named function rather than an inline call so that `_dummy_hash()`'s own first-call
    derivation also runs in the worker thread — see the `asyncio.to_thread` note on
    `verify_login`, which is what keeps it off the event loop.
    """
    return passwords.verify_password(password, _dummy_hash())


def _row(r: aiosqlite.Row) -> User:
    return User(
        id=r["id"],
        email=r["email"],
        role=r["role"],
        password_version=r["password_version"],
        must_change_password=bool(r["must_change_password"]),
        created_at=r["created_at"],
        disabled_at=r["disabled_at"],
    )


def validate_email(email: str) -> str:
    email = email.strip()
    if not email:
        raise ValueError("email must not be empty")
    # C0/C1 controls and DEL would forge audit log lines; checked separately because regex
    # exclusion would be unreadable and would duplicate the existing \s exclusion.
    if not all(ch.isprintable() for ch in email):
        raise ValueError("email must not contain control characters")
    if len(email) > _MAX_EMAIL:
        raise ValueError(f"email must be at most {_MAX_EMAIL} characters")
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError(f"{email!r} is not a valid email address")
    return email


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    return role


async def create_user(
    conn: aiosqlite.Connection,
    email: str,
    password: str,
    *,
    role: str = USER_ROLE,
    must_change_password: bool = True,
) -> User:
    """Create an account. Defaults to must_change_password because accounts are
    admin-provisioned with a generated initial password."""
    email = validate_email(email)
    role = validate_role(role)
    passwords.validate_password(password)
    record = User(
        id=uuid.uuid4().hex,
        email=email,
        role=role,
        password_version=1,
        must_change_password=must_change_password,
        created_at=_now(),
        disabled_at=None,
    )
    # Derived before the INSERT, and off the loop like every other derivation. Keeping it ahead of
    # the write is also what preserves the no-await-before-commit invariant below: the only await
    # this adds is before any DML, never between the write and the commit.
    password_hash = await asyncio.to_thread(passwords.hash_password, password)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, password_version, role,"
            " must_change_password, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.email,
                password_hash,
                record.password_version,
                record.role,
                int(record.must_change_password),
                record.created_at,
            ),
        )
    except sqlite3.IntegrityError as exc:
        # Mirrors keys.create_key: the failed INSERT aborts the statement but leaves the
        # implicit transaction open, and the server shares one long-lived connection — without
        # this rollback a single 409 would hold the WAL write lock against every other writer.
        await conn.rollback()
        raise NameTaken(f"an account for {email!r} already exists") from exc
    await conn.commit()
    return record


async def get_user_by_email(conn: aiosqlite.Connection, email: str) -> User | None:
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
    )
    r = await cur.fetchone()
    return _row(r) if r else None


async def get_user_id_or_raise(conn: aiosqlite.Connection, email: str) -> str:
    """The id for `email`, or a `LookupError` pointing at `auris users add`.

    Shared by every local CLI `--owner` flag (`auris voices`, `auris tts`, `auris say`) so the
    wording for an unknown address can't drift between them — a caller-facing helper, unlike
    `get_user_by_email`, which stays a plain lookup for callers (the HTTP surface's
    `owner_id_for`) that must not reveal whether an address has an account.
    """
    user = await get_user_by_email(conn, email)
    if user is None:
        raise LookupError(f"no account for {email!r} — create one with 'auris users add'")
    return user.id


async def get_user_by_id(conn: aiosqlite.Connection, user_id: str) -> User | None:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,))
    r = await cur.fetchone()
    return _row(r) if r else None


async def list_users(conn: aiosqlite.Connection) -> list[User]:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM users ORDER BY email COLLATE NOCASE")
    return [_row(r) for r in await cur.fetchall()]


async def verify_login(conn: aiosqlite.Connection, email: str, password: str) -> User | None:
    """The account iff the password matches and the account is active, else None.

    One return value for "no such account", "wrong password" and "disabled", because the caller
    must not be able to tell them apart (see the spec: revealing that an email is registered is
    a real disclosure).
    """
    cur = await conn.execute(
        f"SELECT {_COLUMNS}, password_hash FROM users WHERE email = ? COLLATE NOCASE",
        (email.strip(),),
    )
    r = await cur.fetchone()
    # Both branches derive exactly once (the timing equaliser) and both derive in a worker thread.
    # scrypt is ~42 ms of synchronous C, and this is reached from `POST /auth/login`, the one route
    # that needs no credential at all. Left on the event loop it blocked every other request on the
    # server — 20 concurrent sign-in attempts stalled the loop by ~870 ms, against ~9 ms this way —
    # so an unauthenticated caller inside the perimeter could degrade TTS, STT and the dashboard.
    # scrypt releases the GIL, so this is also ~12x faster in aggregate. Keep these awaits here;
    # do not "simplify" them back to direct calls.
    if r is None:
        await asyncio.to_thread(_verify_dummy, password)
        return None
    if not await asyncio.to_thread(passwords.verify_password, password, r["password_hash"]):
        return None
    user = _row(r)
    return user if user.is_active else None


async def set_password(
    conn: aiosqlite.Connection, user_id: str, new_password: str, *, must_change: bool = False
) -> User:
    """Replace the password and bump `password_version`.

    The version bump is what makes a password change end every live session for this user — the
    cookie carries the version, and `auth.resolve_session` re-checks it.
    """
    passwords.validate_password(new_password)
    existing = await get_user_by_id(conn, user_id)
    if existing is None:
        raise LookupError(f"no account with id {user_id!r}")
    # Derived before the UPDATE: off the event loop, and deliberately ahead of the write so no
    # await lands between the write and the commit (see the invariant note in `set_disabled`).
    new_hash = await asyncio.to_thread(passwords.hash_password, new_password)
    await conn.execute(
        "UPDATE users SET password_hash = ?, password_version = password_version + 1,"
        " must_change_password = ? WHERE id = ?",
        (new_hash, int(must_change), user_id),
    )
    await conn.commit()
    updated = await get_user_by_id(conn, user_id)
    assert updated is not None  # just read it back inside the same connection
    return updated


async def set_disabled(
    conn: aiosqlite.Connection, email: str, disabled: bool, *, protect_last_admin: bool = False
) -> User:
    """Disable or re-enable an account.

    With `protect_last_admin`, the "is there another active admin?" test is part of the UPDATE's
    WHERE clause, so it cannot be raced: a concurrent disable on the same connection sees the
    first one's pending change and its own condition then fails.
    """
    existing = await get_user_by_email(conn, email)
    if existing is None:
        raise LookupError(f"no account for {email!r}")
    if disabled and protect_last_admin:
        if not existing.is_active:
            return existing  # already disabled; there is nothing left to protect
        cur = await conn.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?"
            " AND (role != ? OR (SELECT COUNT(*) FROM users"
            "                    WHERE role = ? AND disabled_at IS NULL) > 1)",
            (_now(), existing.id, ADMIN_ROLE, ADMIN_ROLE),
        )
        if cur.rowcount == 0:
            # This rollback releases the write lock, but it is on the *shared* server connection,
            # so it would also discard any other coroutine's uncommitted write. That is safe only
            # because of the invariant below: nobody is ever holding one open across an await.
            await conn.rollback()
            raise LastActiveAdmin(
                "this is the last active admin account; add another admin before disabling it"
            )
    else:
        await conn.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?",
            (_now() if disabled else None, existing.id),
        )
    # DO NOT put an await between a successful write above and this commit. Every writer in this
    # package keeps that gap await-free, which is what makes the shared-connection rollback above
    # (and the ones in create_user/keys.create_key/speakers.rename_speaker) harmless: no coroutine
    # is ever suspended holding an uncommitted write, so no rollback can throw away someone else's
    # work. Insert an await here — an audit log, a notification, one more lookup — and a concurrent
    # refusal can silently discard this UPDATE while this call still returns success.
    await conn.commit()
    updated = await get_user_by_id(conn, existing.id)
    assert updated is not None
    return updated


async def emails_by_id(conn: aiosqlite.Connection, ids: set[str]) -> dict[str, str]:
    """Map the given user ids to their emails, for rendering a voice's owner.

    `IN ()` is a syntax error in SQLite, so the empty set is handled before building the query —
    it is also the common case for a response with no cloned voices at all.
    """
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    cur = await conn.execute(
        f"SELECT id, email FROM users WHERE id IN ({placeholders})", tuple(ids)
    )
    return {r["id"]: r["email"] for r in await cur.fetchall()}


async def count_active_admins(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = ? AND disabled_at IS NULL", (ADMIN_ROLE,)
    )
    r = await cur.fetchone()
    return int(r["n"])
