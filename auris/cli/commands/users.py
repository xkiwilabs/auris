from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite
import typer

from auris.client.mode import remote_client
from auris.config import load_settings
from auris.voice_library import db, passwords, users
from auris.voice_library.exceptions import LastActiveAdmin, NameTaken

app = typer.Typer(help="Create and manage dashboard accounts (admin, on the box).")

T = TypeVar("T")


def _refuse_if_remote() -> None:
    """These verbs write the *server's* users table, so they only mean anything on the box.

    Mirrors `auris keys`: there is no remote fallback to take, because writing the client
    machine's own `data/auris.db` would look like success while the server never reads it.
    """
    client = remote_client()
    if client is None:
        return
    typer.echo(
        "auris users manages the server's own accounts, so it must run on the server box.\n"
        f"This machine is configured against {client.base_url} — use the admin API there "
        "(GET/POST /v1/users, POST /v1/users/{email}/reset) with the master token.",
        err=True,
    )
    raise typer.Exit(code=1)


async def _with_db(fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        return await fn(conn)
    finally:
        await conn.close()


def _run(coro_fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
    """Run a DB coroutine, turning domain errors into a clean exit-1 with a message on stderr.

    Same shape as every other auris command (see docs/architecture.md on the CLI conventions).
    """
    try:
        return asyncio.run(_with_db(coro_fn))
    except (ValueError, LookupError, NameTaken, LastActiveAdmin) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _show_password(email: str, password: str) -> None:
    # The password goes directly under its label, with the reminder after — do not move it to the
    # last line to make a test's parsing easier; read the labelled line instead.
    typer.echo(f"Initial password for {email} (you will not see it again):")
    typer.echo(password)
    typer.echo("They must change it on first sign-in.")


@app.command("add")
def add(
    email: str = typer.Argument(..., help="The person's email address, e.g. alice@example.com"),
    role: str = typer.Option("user", "--role", help="'user' or 'admin'"),
) -> None:
    """Create an account with a generated initial password, printed ONCE.

    The password is generated rather than chosen: it keeps credentials out of shell history and
    removes the "one Welcome123 for everyone" failure mode.
    """
    _refuse_if_remote()
    password = passwords.generate_password()
    user = _run(lambda c: users.create_user(c, email, password, role=role))
    _show_password(user.email, password)


@app.command("list")
def list_users() -> None:
    """List accounts (never passwords — only their hashes are stored)."""
    _refuse_if_remote()
    records = _run(users.list_users)
    if not records:
        typer.echo("no accounts yet — create one with: auris users add <email> --role admin")
        return
    for u in records:
        state = f"disabled {u.disabled_at[:10]}" if u.disabled_at else "active"
        pending = "must change password" if u.must_change_password else ""
        typer.echo(f"{u.email:<36} {u.role:<6} {state:<20} {pending}")


@app.command("reset")
def reset(email: str = typer.Argument(..., help="Account to reset")) -> None:
    """Set a new generated password and force a change on next sign-in.

    There is no email delivery, so hand the new password over out of band.
    """
    _refuse_if_remote()
    password = passwords.generate_password()

    async def go(conn: aiosqlite.Connection) -> users.User:
        user = await users.get_user_by_email(conn, email)
        if user is None:
            raise LookupError(f"no account for {email!r}")
        return await users.set_password(conn, user.id, password, must_change=True)

    user = _run(go)
    _show_password(user.email, password)


@app.command("disable")
def disable(email: str = typer.Argument(..., help="Account to disable")) -> None:
    """Disable an account. Its live dashboard sessions stop working on the next request.

    The last-active-admin refusal lives inside `users.set_disabled`'s UPDATE, so it is shared with
    the admin API and cannot be raced by two concurrent callers. Do NOT re-implement the check
    here with a preceding `count_active_admins` — that is the racy shape Task 8 had to remove.
    """
    _refuse_if_remote()
    record = _run(lambda c: users.set_disabled(c, email, True, protect_last_admin=True))
    typer.echo(f"disabled {record.email}")


@app.command("enable")
def enable(email: str = typer.Argument(..., help="Account to re-enable")) -> None:
    """Re-enable a disabled account."""
    _refuse_if_remote()
    typer.echo(f"enabled {_run(lambda c: users.set_disabled(c, email, False)).email}")
