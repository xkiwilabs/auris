from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite
import typer

from auris.client.mode import remote_client
from auris.config import load_settings
from auris.voice_library import db, keys, users
from auris.voice_library.exceptions import NameTaken

app = typer.Typer(
    help="Issue and revoke API keys for remote users and scripts (admin, on the box)."
)

T = TypeVar("T")


def _refuse_if_remote() -> None:
    """These verbs write the *server's* key table, so they only mean anything on the server box.

    Unlike the other verbs there is no remote fallback to take: silently issuing a key into the
    client machine's own `data/auris.db` would look like success while the server never reads it.
    """
    client = remote_client()
    if client is None:
        return
    typer.echo(
        "auris keys manages the server's own key table, so it must run on the server box.\n"
        f"This machine is configured against {client.base_url} — use the admin API there "
        "(GET/POST /v1/keys, DELETE /v1/keys/{name}) with the master token.",
        err=True,
    )
    raise typer.Exit(code=1)


async def _with_db(fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
    """Run `fn` against the local voice-library DB, creating the schema first (idempotent)."""
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        return await fn(conn)
    finally:
        await conn.close()


async def _resolve_owner(conn, owner_email: str) -> str:
    """The id of the account `--owner` names, or a ValueError pointing at `auris users add`.

    Required, not optional: the bootstrap order on a new box is an account first, then a key
    for it (docs/architecture.md documents this), and a key with no owner can authenticate but can never
    create or modify a voice (see `voice_access.find_for_clone`) — so there is no good reason
    to let one through here, only a confusing one to hit later.
    """
    user = await users.get_user_by_email(conn, owner_email)
    if user is None:
        raise ValueError(
            f"no account for {owner_email!r} — create one first with: auris users add {owner_email}"
        )
    return user.id


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Who this key is for, e.g. alice or ci-bot"),
    owner: str = typer.Option(..., "--owner", help="Email of the account that will own this key"),
) -> None:
    """Issue a new key, owned by `--owner`. The key is printed ONCE — copy it now."""
    _refuse_if_remote()

    async def go(c):
        owner_id = await _resolve_owner(c, owner)
        return await keys.create_key(c, name, owner_id=owner_id)

    try:
        record, plaintext = asyncio.run(_with_db(go))
    except (ValueError, NameTaken) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Issued API key for {record.name} (you will not see it again):")
    typer.echo(plaintext)


@app.command("list")
def list_keys() -> None:
    """List keys (names only — the secrets are never stored)."""
    _refuse_if_remote()

    async def go(c):
        records = await keys.list_keys(c)
        emails = {u.id: u.email for u in await users.list_users(c)}
        return records, emails

    records, emails = asyncio.run(_with_db(go))
    if not records:
        typer.echo("no API keys yet — issue one with: auris keys add <name> --owner <email>")
        return
    for k in records:
        state = f"revoked {k.revoked_at[:10]}" if k.revoked_at else "active"
        used = f"last used {k.last_used_at[:16]}" if k.last_used_at else "never used"
        owner_label = emails.get(k.owner_id, "(no owner)") if k.owner_id else "(no owner)"
        typer.echo(f"{k.name:<20} {owner_label:<28} {state:<20} {used}")


@app.command("revoke")
def revoke(name: str = typer.Argument(..., help="Key name to revoke")) -> None:
    """Revoke a key. Its live sessions stop working immediately; the name stays reserved."""
    _refuse_if_remote()
    record = asyncio.run(_with_db(lambda c: keys.revoke_key(c, name)))
    if record is None:
        typer.echo(f"API key {name!r} not found", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"revoked {record.name}")
