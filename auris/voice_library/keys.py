from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from auris.voice_library.exceptions import NameTaken

KEY_PREFIX = "auris_"
# The master token's principal. A key may not take this name: the principal goes into the
# structlog context of every request, and a key called "admin" would make its log lines
# indistinguishable from the real admin's.
RESERVED_NAMES = frozenset({"admin"})

# A key name is a path segment in `DELETE /v1/keys/{name}` and a field in every audit log line,
# so the charset is deliberately narrow. A name containing "/" would mint a key that could never
# be revoked over HTTP (Starlette percent-decodes before routing, so even %2F misses the route);
# newlines and control characters would let a name forge extra log lines.
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass
class ApiKey:
    id: str
    name: str
    created_at: str
    revoked_at: str | None
    last_used_at: str | None
    owner_id: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """Keys are 256 bits of entropy, so a plain SHA-256 is enough — and keeps the per-request
    lookup to one indexed query. (bcrypt is for low-entropy passwords.)"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


_COLUMNS = "id, name, created_at, revoked_at, last_used_at, owner_id"  # never key_hash


def _row(r: aiosqlite.Row) -> ApiKey:
    return ApiKey(
        r["id"], r["name"], r["created_at"], r["revoked_at"], r["last_used_at"], r["owner_id"]
    )


async def create_key(
    conn: aiosqlite.Connection, name: str, owner_id: str | None = None
) -> tuple[ApiKey, str]:
    """Issue a key. Returns (record, plaintext); the plaintext is never stored and never
    returned again. A revoked key's name stays taken so old log lines remain unambiguous.

    `owner_id` stays optional here — the column is nullable and legacy rows exist — but both
    the HTTP route (`routers/keys.py`) and the CLI (`cli/commands/keys.py`) now require an
    owner before they call this, so an ownerless key is only ever a pre-existing one. That
    matters because `voice_access.find_for_clone` refuses an ownerless, non-admin caller before
    it can create or modify a voice: it owns nothing, and letting it claim an unowned voice
    would hand it the local CLI's own legacy voices too. A key minted before this rule still
    authenticates; it just cannot touch voices until an admin re-issues it for an account.
    """
    name = name.strip()
    if not name:
        raise ValueError("key name must not be empty")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            "key name may use only letters, digits, dot, underscore and hyphen (max 64 characters)"
        )
    if name.casefold() in RESERVED_NAMES:
        raise NameTaken(f"{name!r} is reserved for the master token; choose another name")
    plaintext = generate_key()
    record = ApiKey(uuid.uuid4().hex, name, _now(), None, None, owner_id)
    try:
        await conn.execute(
            "INSERT INTO api_keys (id, name, key_hash, created_at, owner_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (record.id, record.name, hash_key(plaintext), record.created_at, owner_id),
        )
    except sqlite3.IntegrityError as exc:
        # The failed INSERT aborts the statement but leaves the implicit transaction open, and
        # the server shares one long-lived connection — without this rollback a single 409 would
        # hold the WAL write lock against every other writer until some later request commits.
        await conn.rollback()
        raise NameTaken(f"an API key named {name!r} already exists") from exc
    await conn.commit()
    return record, plaintext


async def list_keys(conn: aiosqlite.Connection, owner_id: str | None = None) -> list[ApiKey]:
    """Every key, or only those owned by `owner_id`.

    None means "no filter", not "unowned": an admin listing everything passes None. There is
    deliberately no way to list *only* unowned keys — an admin sees them in the full list with
    `owner_id: null`.
    """
    sql = f"SELECT {_COLUMNS} FROM api_keys"
    params: tuple[str, ...] = ()
    if owner_id is not None:
        sql += " WHERE owner_id = ?"
        params = (owner_id,)
    cur = await conn.execute(sql + " ORDER BY name COLLATE NOCASE", params)
    return [_row(r) for r in await cur.fetchall()]


async def get_key_by_name(conn: aiosqlite.Connection, name: str) -> ApiKey | None:
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM api_keys WHERE name = ? COLLATE NOCASE", (name,)
    )
    r = await cur.fetchone()
    return _row(r) if r else None


async def revoke_key(conn: aiosqlite.Connection, name: str) -> ApiKey | None:
    """Soft-revoke (the row survives for the audit trail). Idempotent; None if unknown."""
    existing = await get_key_by_name(conn, name)
    if existing is None:
        return None
    if existing.revoked_at is None:
        await conn.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ?", (_now(), existing.id))
        await conn.commit()
    return await get_key_by_name(conn, name)


async def lookup_active(conn: aiosqlite.Connection, key: str) -> ApiKey | None:
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
        (hash_key(key),),
    )
    r = await cur.fetchone()
    return _row(r) if r else None


async def touch_last_used(conn: aiosqlite.Connection, key_id: str) -> None:
    await conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), key_id))
    await conn.commit()
