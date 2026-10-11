from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite
import typer

from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.voice_library import db, speakers, users
from auris.voice_library.exceptions import AmbiguousVoice, NameTaken
from auris.voice_library.resolve import describe_ambiguity, preset_voice_rows

app = typer.Typer(help="List and manage voices.", invoke_without_command=True)

T = TypeVar("T")


def _refuse_if_remote() -> None:
    """`adopt` writes the *server's* own speakers table, so it only means anything on the box.

    Mirrors `auris users`/`auris keys`: there is no remote fallback to take, because writing the
    client machine's own `data/auris.db` would look like success while the server never reads it.
    """
    client = remote_client()
    if client is None:
        return
    typer.echo(
        "auris voices adopt assigns ownership in the server's own database, so it must run on "
        f"the server box.\nThis machine is configured against {client.base_url} — run it there "
        "instead.",
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
    `AmbiguousVoice` is in the tuple because it is a plain `Exception`, not a `ValueError`: a
    duplicated voice name is the same condition here as it is for `auris tts`, so the two raise
    the same type and only the catch site has to know it.
    """
    try:
        return asyncio.run(_with_db(coro_fn))
    except (ValueError, LookupError, AmbiguousVoice) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _render(voices_list: list[dict], empty_note: str | None = None) -> None:
    presets = [v for v in voices_list if v.get("kind") == "preset"]
    cloned = [v for v in voices_list if v.get("kind") != "preset"]
    typer.echo("Presets (built-in, usable now):")
    for p in presets:
        typer.echo(f"  {p['name']:<16} [preset]  {p.get('description') or ''}")
    typer.echo("\nCloned voices:")
    if not cloned:
        # `empty_note` replaces the clone hint when a filter, not an empty library, is why
        # there is nothing to show — "run 'auris clone'" would be wrong advice there.
        typer.echo("  " + (empty_note or "(none yet — run 'auris clone' or 'auris record')"))
    for c in cloned:
        clips = c.get("clips", 0)
        plural = "" if clips == 1 else "s"
        owner = c.get("owner") or "(unowned)"
        visibility = c.get("visibility") or "private"
        typer.echo(
            f"  {c['name']:<16} {clips} clip{plural} · {c.get('duration_s', 0.0):.1f}s"
            f" · {visibility:<7} · {owner}"
        )


async def _list_rows(owner: str | None) -> list[dict]:
    """The operator's unscoped (`viewer=None`) view of every voice, optionally narrowed to one
    owner's. `--owner` is a local filter, not a server-side scope: the local CLI always sees
    everything (see the unscoped-viewer note on `speakers.list_visible_with_stats`)."""
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        owner_id = None
        if owner is not None:
            owner_id = await users.get_user_id_or_raise(conn, owner)
        stats = await speakers.list_visible_with_stats(conn, None)
        if owner_id is not None:
            stats = [row for row in stats if row[4] == owner_id]
        emails = await users.emails_by_id(conn, {row[4] for row in stats if row[4]})
    finally:
        await conn.close()
    return list(preset_voice_rows()) + [
        {
            "name": n,
            "kind": "cloned",
            "clips": c,
            "duration_s": d,
            "owner": emails.get(o),
            "visibility": vis,
        }
        for (n, c, d, _m, o, vis) in stats
    ]


def _remote_owner_filter(voices_list: list[dict], owner: str) -> tuple[list[dict], str | None]:
    """Narrow a `/v1/voices` listing to one owner, matching what local mode does. Returns the
    filtered rows and, when nothing matched, the line to print in place of the clone hint.

    Emails are compared casefolded because every email index in the schema is `COLLATE NOCASE`
    and `users.validate_email` only strips — stored addresses can differ in case from what the
    operator types, so a plain `==` made `--owner Alice@Example.COM` silently return nothing
    here while local mode found the voices.

    Nothing matching names the address back rather than printing "(none yet — run 'auris
    clone')", which was wrong twice: voices may well exist, and cloning would not help. But it
    stays **exit 0**, and the message claims nothing about whether the account exists. Over HTTP
    it cannot: `/v1/voices` exposes only the owners of voices this caller may *see*, so a client
    genuinely cannot tell "no such account" from "nothing of theirs is visible to you", and both
    cases must read identically or the exit code itself discloses which addresses are
    registered. An operator asking about a real account that simply has no voices did nothing
    wrong, so they must not get an error either. Local mode keeps its `auris users add` hint and
    its exit 1, which is safe there: the operator is on the box and can already read the table.
    """
    wanted = owner.strip().casefold()
    matched = [
        v
        for v in voices_list
        if v.get("kind") != "preset" and (v.get("owner") or "").strip().casefold() == wanted
    ]
    if not matched:
        return [v for v in voices_list if v.get("kind") == "preset"], (
            f"no voices visible for {owner!r}"
        )
    return [v for v in voices_list if v.get("kind") == "preset"] + matched, None


def _list_and_render(owner: str | None) -> None:
    sc = remote_client()
    if sc is not None:
        try:
            voices_list = sc.list_voices()
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        empty_note = None
        if owner is not None:
            voices_list, empty_note = _remote_owner_filter(voices_list, owner)
        _render(voices_list, empty_note)
        return
    try:
        rows = asyncio.run(_list_rows(owner))
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    _render(rows)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    owner: str | None = typer.Option(
        None, "--owner", help="Only this account's voices (also accepted after a subcommand)"
    ),
) -> None:
    """List available voices: built-in presets and cloned speakers."""
    # Stashed for the subcommands even when we do not use it ourselves — see `_owner_for`.
    ctx.obj = owner
    if ctx.invoked_subcommand is None:
        _list_and_render(owner)


def _owner_for(ctx: typer.Context, owner: str | None) -> str | None:
    """The effective `--owner`, whichever side of the subcommand it was typed on.

    Typer accepts `--owner` in both positions, because the group callback declares it (for the
    listing filter) and so does each subcommand. The callback only *read* its copy when no
    subcommand ran, so `auris voices --owner bob@example.com share solo` — the position
    `auris voices --help` advertises — dropped the flag without a word and then resolved
    whichever `solo` the no-owner precedence found: Alice's private voice became readable by
    every account, reported as success. A dropped disambiguator does not fail, it acts on a
    different account's row, so neither position may be silently ignored.

    Two different values is a contradiction rather than a precedence question, so refuse and
    name both. Compared casefolded, because the `users` email index is `COLLATE NOCASE`: the
    same address typed with different capitalisation on the two sides is one request.
    """
    group = ctx.obj if isinstance(ctx.obj, str) else None
    if group is None:
        return owner
    if owner is None:
        return group
    if group.strip().casefold() != owner.strip().casefold():
        typer.echo(
            f"--owner was given twice with different values: {group!r} before the subcommand "
            f"and {owner!r} after it. Pass it once.",
            err=True,
        )
        raise typer.Exit(code=1)
    return owner


async def _resolve_write_target(
    conn: aiosqlite.Connection, name: str, owner: str | None
) -> speakers.Speaker:
    """Pick exactly one voice named `name` for `share`/`unshare` to act on.

    Mirrors `resolve_voice`'s own preset → own → shared precedence, adapted for the operator's
    unscoped view (there are no presets here, and "own" is the unowned namespace rather than an
    account's):

    1. `--owner <email>` given → the exact (name, owner) row, or a clean not-found/no-account
       error. An explicit disambiguator always wins and never falls through.
    2. Otherwise, an *unowned* voice with this name wins — the operator's own namespace, exactly
       as the `clone.py` fix resolves it.
    3. Otherwise, if exactly one voice anywhere has this name, it is the unambiguous target.
       Deliberate reach over a user's voice from the box is legitimate (the operator is admin
       there), but it must never happen *silently* — so it is only reached once step 2 finds
       nothing to claim as the operator's own.
    4. Otherwise two or more owners hold the name: refuse rather than silently pick one.
    """
    if owner is not None:
        owner_id = await users.get_user_id_or_raise(conn, owner)
        sp = await speakers.get_speaker_by_name_and_owner(conn, name, owner_id)
        if sp is None:
            raise LookupError(f"voice {name!r} not found for {owner!r}")
        return sp

    unowned = await speakers.get_speaker_by_name_and_owner(conn, name, None)
    if unowned is not None:
        return unowned

    matches = await speakers.visible_speakers_by_name(conn, name, None)
    if not matches:
        raise LookupError(f"voice {name!r} not found")
    if len(matches) == 1:
        return matches[0]
    # The same `AmbiguousVoice` the resolution paths raise, so a duplicated name is one
    # exception type across all four implementations of the precedence, and `owners` is on it
    # here too. The message is the shared local one (`describe_ambiguity`), not the count-only
    # HTTP wording: see that function on why the two differ deliberately.
    raise AmbiguousVoice(
        await describe_ambiguity(conn, name, tuple(s.owner_id for s in matches)),
        owners=tuple(s.owner_id for s in matches),
    )


def _set_visibility(name: str, visibility: str, owner: str | None) -> None:
    """Resolve `name` (see `_resolve_write_target`) and change its visibility."""

    async def go(conn: aiosqlite.Connection) -> speakers.Speaker:
        sp = await _resolve_write_target(conn, name, owner)
        # A local mirror of the rule `PATCH /v1/voices/{name}` enforces (→ 422), from the one
        # shared implementation, so the box and the service cannot answer this differently.
        speakers.refuse_inert_private(sp, visibility)
        return await speakers.set_visibility(conn, sp.id, visibility)

    updated = _run(go)
    _report(updated.name, updated.visibility, updated.owner_id)


def _report(name: str, visibility: str, owner_id: str | None) -> None:
    """What `share`/`unshare` print, from whichever mode ran — the remote path passes the
    PATCH response's `owner`, so the note below cannot go missing over HTTP the way it did."""
    typer.echo(f"{name}: {visibility}")
    if visibility == speakers.SHARED and owner_id is None:
        typer.echo(f"note: {name!r} has no owner, so it was already visible to every account.")


def _change_visibility(ctx: typer.Context, name: str, visibility: str, owner: str | None) -> None:
    """`share`/`unshare`'s shared body: resolve `--owner` from either position, then act.

    Remote mode PATCHes the service, which applies the same rules (including the unowned-voice
    refusal, which used to exist only on the box), so the two modes agree.
    """
    owner = _owner_for(ctx, owner)
    sc = remote_client()
    if sc is not None:  # an ordinary per-voice operation the API already exposes
        try:
            info = sc.update_voice(name, visibility=visibility, owner=owner)
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        # `VoiceInfo` carries `owner`, so the unowned-voice note is the same here as locally.
        _report(info["name"], info["visibility"], info.get("owner"))
        return
    _set_visibility(name, visibility, owner)


@app.command("share")
def share(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Voice to make visible to everyone"),
    owner: str | None = typer.Option(None, "--owner", help="Disambiguate a duplicated name"),
) -> None:
    """Make a voice visible to every signed-in account. They can use it, not change it."""
    _change_visibility(ctx, name, speakers.SHARED, owner)


@app.command("unshare")
def unshare(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Voice to make private again"),
    owner: str | None = typer.Option(None, "--owner", help="Disambiguate a duplicated name"),
) -> None:
    """Make a voice visible only to its owner (and admins)."""
    _change_visibility(ctx, name, speakers.PRIVATE, owner)


@app.command("adopt")
def adopt(
    ctx: typer.Context,
    email: str = typer.Argument(..., help="Account to assign unowned voices to"),
) -> None:
    """Assign every unowned voice to an account.

    Voices cloned before accounts existed have no owner: the migration leaves them shared and
    admin-only on purpose, because at migration time there may be no accounts at all to assign
    them to. Run this once after deploying. Idempotent.
    """
    if ctx.obj is not None:
        # The group's `--owner` is meaningless here — the account is the argument — and an
        # accepted-then-ignored flag is what made `share` write the wrong account's row.
        typer.echo(
            "auris voices adopt takes the account as its argument, so --owner does not apply: "
            f"run 'auris voices adopt {email}'.",
            err=True,
        )
        raise typer.Exit(code=1)
    _refuse_if_remote()

    async def go(conn: aiosqlite.Connection) -> int:
        user = await users.get_user_by_email(conn, email)
        if user is None:
            raise LookupError(f"no account for {email!r}")
        try:
            return await speakers.adopt_unowned(conn, user.id)
        except NameTaken as exc:
            # Re-raised as a ValueError so `_run` renders it like any other domain error. The
            # library names the clashing voices; only here do we know the account's email and
            # the command to re-run. The expected state of a box that has both legacy voices
            # and accounts, so it has to read as an instruction, not a traceback.
            raise ValueError(
                f"nothing was adopted: {exc}. Rename or delete one voice of each clashing "
                f"pair, then run 'auris voices adopt {email}' again."
            ) from exc

    count = _run(go)
    typer.echo(f"adopted {count} voice(s) for {email}")
