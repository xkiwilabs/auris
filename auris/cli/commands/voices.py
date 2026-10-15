from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite
import numpy as np
import soundfile as sf
import typer

from auris.audio import io, reference
from auris.audio.transcribe import transcribe_detailed, transcribe_reference
from auris.audio.transcript import TranscribeOptions
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.models import build_model
from auris.models.base import PreparedReference
from auris.voice_library import blends, cloning, db, speakers, storage, users
from auris.voice_library.exceptions import AmbiguousVoice, NameTaken
from auris.voice_library.resolve import describe_ambiguity, is_preset, preset_voice_rows

app = typer.Typer(help="List and manage voices.", invoke_without_command=True)

T = TypeVar("T")


def _refuse_if_remote(
    command: str = "adopt", does: str = "assigns ownership in the server's own database"
) -> None:
    """`adopt` and `refit` write the *server's* own library, so they only mean anything on the box.

    Mirrors `auris users`/`auris keys`: there is no remote fallback to take, because writing the
    client machine's own `data/auris.db` would look like success while the server never reads it.
    """
    client = remote_client()
    if client is None:
        return
    typer.echo(
        f"auris voices {command} {does}, so it must run on the server box.\nThis machine is "
        f"configured against {client.base_url} — run it there instead.",
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
    typer.echo("\nCloned and blended voices:")
    if not cloned:
        # `empty_note` replaces the clone hint when a filter, not an empty library, is why
        # there is nothing to show — "run 'auris clone'" would be wrong advice there.
        typer.echo("  " + (empty_note or "(none yet — run 'auris clone' or 'auris record')"))
    for c in cloned:
        owner = c.get("owner") or "(unowned)"
        visibility = c.get("visibility") or "private"
        if c.get("kind") == "blend":
            what = "blend: " + " + ".join(
                f"{round(part['weight'] * 100)}% {part['voice']}" for part in c.get("blend") or []
            )
        else:
            clips = c.get("clips", 0)
            what = f"{clips} clip{'' if clips == 1 else 's'} · {c.get('duration_s', 0.0):.1f}s"
        typer.echo(f"  {c['name']:<16} {what} · {visibility:<7} · {owner}")


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
            "kind": "blend" if b else "cloned",
            "clips": c,
            "duration_s": d,
            "owner": emails.get(o),
            "visibility": vis,
            "blend": blends.from_json(b).public() if b else None,
        }
        for (n, c, d, _m, o, vis, b) in stats
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


@app.command("blend")
def blend(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name for the blended voice (new, or one to re-mix)"),
    parts: list[str] = typer.Argument(
        ..., help="2 or 3 built-in voices as NAME=WEIGHT, e.g. narrator-uk-f=60 bright-uk-f=40"
    ),
    share: bool | None = typer.Option(
        None, "--share/--private", help="Who can use it (default: private when new, else as is)"
    ),
) -> None:
    """Mix 2–3 built-in voices into a new voice, or re-mix an existing blend.

    Weights are any positive numbers and are scaled to add up to 100%. Only built-in voices can
    be blended: a cloned voice is a recording, and two recordings can't be averaged.
    """
    if ctx.obj is not None:
        # A blend is always written to the caller's own namespace, as `auris clone` is, so
        # there is no other account's voice for `--owner` to pick.
        typer.echo("auris voices blend writes your own voice, so --owner does not apply.", err=True)
        raise typer.Exit(code=1)
    try:
        if is_preset(name):
            raise ValueError(f"{name!r} is a built-in preset name; choose a different voice name")
        items = [blends.parse_arg(p) for p in parts]
        mix = blends.make(items)  # validated here too, so a typo fails before any call
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    visibility = None if share is None else (speakers.SHARED if share else speakers.PRIVATE)
    sc = remote_client()
    if sc is not None:
        try:
            info = sc.put_blend(name, items, visibility)
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{info['name']}: {info['description']} ({info['visibility']})")
        return

    async def go(conn: aiosqlite.Connection) -> tuple[speakers.Speaker, bool]:
        # On the box the operator's voices are unowned, so always shared (see
        # UNOWNED_IS_ALWAYS_SHARED): `--private` is refused by put_blend with the reason.
        return await speakers.put_blend(conn, name, None, blends.to_json(mix), visibility)

    try:
        sp, created = _run(go)
    except NameTaken as exc:  # a concurrent create; not a ValueError, so _run lets it through
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    verb = "created" if created else "re-mixed"
    typer.echo(f"{verb} {sp.name}: {mix.describe()} ({sp.visibility})")


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


# Fewer words a second than this and the transcript cannot be describing the audio — Whisper
# hallucinating "Thank you." over noise or music. Conversational English runs at 2–3 a second.
_SPARSE_WORDS_PER_S = 0.8

# What refit has each voice say, listening for words that were never asked for. Varied openings,
# because a leak is the reference's last word glued to the start of whatever comes next.
PROBE_TEXTS = (
    "The weather today is cold and windy.",
    "Please remember to bring your laptop to the meeting tomorrow morning.",
    "We measured reaction times across three conditions.",
    "Thank you all for coming along this afternoon.",
    "Every Tuesday the library opens a little later than usual.",
    "Our results suggest that practice improves performance.",
)
# An intact reference still leaks now and then (1 in 24 on a measured voice), so one stray word
# in six is noise; two or more is a reference worth cutting back (a clipped one leaked in 22/24).
LEAK_LIMIT = 2


def _same_words(a: str, b: str) -> bool:
    norm = lambda t: " ".join("".join(c for c in t.casefold() if c.isalnum() or c in " '").split())  # noqa: E731
    return norm(a) == norm(b)


def _load_probe_model(settings):
    """The f5-TTS model refit listens through. Its own seam so tests never load weights."""
    model = build_model("f5_tts", settings)
    model.load()
    return model


def _leaks(model, settings, audio: np.ndarray, sr: int, transcript: str) -> int:
    """How many of PROBE_TEXTS come back with words that were never asked for."""
    ref = PreparedReference(audio=audio, sample_rate=sr, transcript=transcript)
    heard_opts = TranscribeOptions(language="en", vad_filter=False)
    count = 0
    for text in PROBE_TEXTS:
        wav = model.synthesise(text, ref)
        heard = transcribe_detailed(wav, model.sample_rate, heard_opts, settings).text
        count += bool(reference.extra_leading_words(text, heard))
    return count


@app.command("refit")
def refit(
    ctx: typer.Context,
    names: list[str] = typer.Argument(None, help="Voices to check (default: every voice)"),
    owner: str | None = typer.Option(None, "--owner", help="Only this account's voices"),
    apply: bool = typer.Option(False, "--apply", help="Store the fixes (default: report only)"),
    retranscribe: bool = typer.Option(
        False, "--retranscribe", help="Also replace transcripts the model now hears differently"
    ),
    asr_model: str = typer.Option(
        None, "--asr-model", help="Speech-to-text model (default: AURIS_ASR_MODEL)"
    ),
) -> None:
    """Test every voice for stray words and fix the ones whose reference makes them.

    A reference that ends part-way through a word makes the voice say that word (or part of it)
    before whatever it was asked to say. Nothing in the audio reliably shows a cut word, so each
    voice is simply *tested*: it speaks six sentences and a speech-to-text model listens for
    words that were never asked for. A voice with two or more is cut back to an earlier pause,
    re-tested, and fixed only if that leaks less. Report only unless --apply. Applying adds a
    new clip (synthesis uses the newest) and keeps the old one, so deleting it undoes the fix.
    Needs the model extra and runs both models, so allow a few seconds per voice.
    """
    owner = _owner_for(ctx, owner)
    _refuse_if_remote("refit", "re-cuts reference clips in the server's own voice library")
    settings = load_settings()
    n = len(PROBE_TEXTS)

    async def go(conn: aiosqlite.Connection) -> tuple[int, int]:
        if names:
            targets = [await _resolve_write_target(conn, name, owner) for name in names]
        else:
            targets = await speakers.list_all(conn)
            if owner is not None:
                owner_id = await users.get_user_id_or_raise(conn, owner)
                targets = [sp for sp in targets if sp.owner_id == owner_id]
        targets = [sp for sp in targets if not sp.is_blend]  # a recipe has no recording to test
        emails = await users.emails_by_id(conn, {sp.owner_id for sp in targets if sp.owner_id})
        try:
            model = await asyncio.to_thread(_load_probe_model, settings) if targets else None
        except (ImportError, RuntimeError) as exc:
            raise ValueError(
                f"refit speaks with f5-TTS, which needs the model extra "
                f"(scripts/install.sh --model): {exc}"
            ) from exc
        fixes = applied = 0
        for sp in targets:
            clips = await speakers.get_clips(conn, sp.id)
            if not clips:
                continue
            clip = clips[-1]
            label = sp.name + (f" ({emails.get(sp.owner_id, sp.owner_id)})" if sp.owner_id else "")
            path = storage.clips_dir(settings.voices_dir, sp.id) / clip.filename
            try:  # one unreadable clip is reported, and the rest are still checked
                audio, sr = await asyncio.to_thread(io.load_audio, path)
                decoded = await asyncio.to_thread(
                    transcribe_reference,
                    reference.decode_window(audio, sr),
                    sr,
                    asr_model,
                    settings,
                )
            except (RuntimeError, OSError, sf.SoundFileError) as exc:
                typer.echo(f"{label}: {clip.filename}\n  skipped: {exc}")
                continue
            seconds = len(audio) / sr
            typer.echo(f"{label}: {clip.filename}, {seconds:.2f} s")
            if len(decoded.text.split()) / max(seconds, 1e-6) < _SPARSE_WORDS_PER_S:
                typer.echo(
                    f"  re-record: the transcript ({decoded.text.strip()!r}) covers little of the "
                    "audio — usually noise or music rather than speech"
                )
                continue
            fixed: tuple[np.ndarray, str] | None = None
            leaks = await asyncio.to_thread(_leaks, model, settings, audio, sr, clip.transcript)
            if leaks >= LEAK_LIMIT:
                words = [w for seg in decoded.segments for w in seg.words]
                fit = reference.fit_reference(audio, sr, words or None)
                if fit.dropped_speech and fit.transcript:
                    after = await asyncio.to_thread(
                        _leaks, model, settings, fit.audio, sr, fit.transcript
                    )
                    if after < leaks:
                        fixed = (fit.audio, fit.transcript)
                        typer.echo(
                            f"  stray words in {leaks}/{n} test sentences; cut back to "
                            f"{len(fit.audio) / sr:.2f} s at a pause: {after}/{n}\n"
                            f"    was: {clip.transcript}\n    now: {fit.transcript}"
                        )
                    else:
                        typer.echo(
                            f"  stray words in {leaks}/{n} test sentences, and cutting back to "
                            f"the last pause didn't help ({after}/{n}) — re-record this voice"
                        )
                else:
                    typer.echo(
                        f"  stray words in {leaks}/{n} test sentences, and there's no earlier "
                        "pause to cut back to — re-record this voice"
                    )
            elif decoded.text.strip() and not _same_words(clip.transcript, decoded.text):
                note = "" if retranscribe else " (--retranscribe to use it)"
                typer.echo(
                    f"  ok ({leaks}/{n} stray); transcript differs{note}\n"
                    f"    was: {clip.transcript}\n    now: {decoded.text.strip()}"
                )
                if retranscribe:
                    fixed = (audio, decoded.text.strip())
            else:
                typer.echo(f"  ok ({leaks}/{n} stray)")
            if seconds < reference.GOOD_LENGTH_S:
                typer.echo(
                    f"  short: {reference.GOOD_LENGTH_S:.0f}–10 s of speech would capture the "
                    "voice better"
                )
            if fixed is None:
                continue
            fixes += 1
            if apply:
                kept, text = fixed
                index = await cloning.add_reference(
                    conn, settings.voices_dir, sp.id, kept, sr, text, len(kept) / sr
                )
                typer.echo(f"  stored clip_{index:03d}.wav (the old clip is kept)")
                applied += 1
        return fixes, applied

    fixes, applied = _run(go)
    if apply:
        typer.echo(f"\n{applied} voice(s) refit.")
    elif fixes:
        typer.echo(f"\nDry run — nothing changed. Run again with --apply to fix {fixes} voice(s).")
    else:
        typer.echo("\nDry run — nothing to fix.")
