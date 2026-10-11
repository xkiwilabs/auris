from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from auris.voice_library.exceptions import NameTaken
from auris.voice_library.viewer import Viewer

PRIVATE = "private"
SHARED = "shared"
_VISIBILITIES = frozenset({PRIVATE, SHARED})


def validate_visibility(visibility: str) -> str:
    if visibility not in _VISIBILITIES:
        raise ValueError(f"visibility must be one of {sorted(_VISIBILITIES)}")
    return visibility


UNOWNED_IS_ALWAYS_SHARED = """An unowned voice is always `shared`. Every writer in this module holds that invariant.

`_VISIBLE` ends `OR s.owner_id IS NULL`, so an unowned row is visible to every account whatever
its `visibility` column says. `private` there is therefore not a stricter setting — it is a
column that contradicts the access everyone actually has, and it misleads twice over: the
operator is told `private` by `auris voices` and the dashboard, and then giving the row an owner
makes the column finally bite and *revokes* access colleagues already had, with no warning.

So one rule at every writer: `create_speaker` derives the default (`default_visibility`) and
refuses an explicit `private` on an unowned row (`refuse_unowned_private`), `set_visibility`'s
callers refuse the same change on an existing row (`refuse_inert_private`), and `db.migrate()`
repairs any row an earlier commit left in the dishonest state.
"""


def default_visibility(owner_id: str | None) -> str:
    """`shared` for an unowned voice, `private` for an owned one.

    An owned voice starts private because its owner can loosen it deliberately; an unowned one
    *is* readable by everyone whatever we store, so `shared` is the only honest column value —
    see `UNOWNED_IS_ALWAYS_SHARED`.
    """
    return PRIVATE if owner_id is not None else SHARED


def refuse_unowned_private(owner_id: str | None, visibility: str, name: str) -> None:
    """Raise ValueError (→ exit 1 locally, 422 over HTTP) for the one dishonest combination:
    `private` on a row with no owner. The single implementation of `UNOWNED_IS_ALWAYS_SHARED`,
    shared by the create path and by `refuse_inert_private`'s update path so the two cannot
    drift. Pure and import-light on purpose — the CLI and the service both reach it.
    """
    if visibility == PRIVATE and owner_id is None:
        raise ValueError(
            f"{name!r} has no owner, so it is visible to every account no matter what "
            "its visibility is set to. Run 'auris voices adopt <email>' to give it an owner "
            "first, then unshare it."
        )


@dataclass
class Speaker:
    id: str
    name: str
    created_at: str
    default_model: str
    default_speed: float
    owner_id: str | None = None
    visibility: str = PRIVATE


@dataclass
class ReferenceClip:
    id: str
    speaker_id: str
    filename: str
    transcript: str
    duration_s: float | None
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# A voice name is a URL path segment (`/v1/voices/{name}/clips`) as well as a CLI argument. A
# name containing "/" is unreachable over HTTP *entirely*: Starlette percent-decodes before
# routing, so neither `/v1/voices/a/b` nor `/v1/voices/a%2Fb` matches, and the voice can then
# never be listed, renamed or deleted through any interface — and unlike an API key there is no
# CLI escape hatch. Control characters would let a name forge log lines. Everything else —
# spaces, apostrophes, accented letters — is allowed, because voices are named after people.
_FORBIDDEN_RE = re.compile(r"[/\\\x00-\x1f\x7f]")
_MAX_LEN = 64


def validate_name(name: str) -> str:
    """Normalise and check a voice name. Returns the stripped name; raises ValueError (→ 422)."""
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > _MAX_LEN:
        raise ValueError(f"voice name is longer than {_MAX_LEN} characters")
    if _FORBIDDEN_RE.search(name):
        raise ValueError("voice name may not contain slashes or control characters")
    return name


def _uid() -> str:
    return uuid.uuid4().hex


def _row(r) -> Speaker:
    return Speaker(
        r["id"],
        r["name"],
        r["created_at"],
        r["default_model"],
        r["default_speed"],
        r["owner_id"],
        r["visibility"],
    )


async def create_speaker(
    conn: aiosqlite.Connection,
    name: str,
    default_model: str = "f5_tts",
    default_speed: float = 1.0,
    owner_id: str | None = None,
    visibility: str | None = None,
) -> Speaker:
    """Insert a voice. `visibility` defaults per `default_visibility` and an unowned row may
    not be `private` — see `UNOWNED_IS_ALWAYS_SHARED`, the invariant every writer here holds.
    """
    visibility = (
        default_visibility(owner_id) if visibility is None else validate_visibility(visibility)
    )
    # Unconditional: the derived default can never trip this, so the only way to reach the
    # refusal is an explicit `private` on an unowned row, which is the dishonest combination.
    refuse_unowned_private(owner_id, visibility, name)
    sp = Speaker(
        _uid(),
        validate_name(name),
        _now(),
        default_model,
        default_speed,
        owner_id,
        visibility,
    )
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, default_model, default_speed, owner_id,"
        " visibility) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            sp.id,
            sp.name,
            sp.created_at,
            sp.default_model,
            sp.default_speed,
            sp.owner_id,
            sp.visibility,
        ),
    )
    await conn.commit()
    return sp


async def add_clip(
    conn: aiosqlite.Connection,
    speaker_id: str,
    filename: str,
    transcript: str,
    duration_s: float | None,
) -> ReferenceClip:
    clip = ReferenceClip(_uid(), speaker_id, filename, transcript, duration_s, _now())
    await conn.execute(
        "INSERT INTO reference_clips "
        "(id, speaker_id, filename, transcript, duration_s, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            clip.id,
            clip.speaker_id,
            clip.filename,
            clip.transcript,
            clip.duration_s,
            clip.created_at,
        ),
    )
    await conn.commit()
    return clip


# A viewer sees: their own voices, anything shared, and legacy unowned rows. An admin sees all.
# `?` params are (user_id, user_id) — a NULL user_id matches nothing in the first clause, which
# is exactly right for the master token and for a pre-accounts key.
_VISIBLE = "(? IS NOT NULL AND s.owner_id = ?) OR s.visibility = 'shared' OR s.owner_id IS NULL"


def _scope(viewer: Viewer | None) -> tuple[str, tuple]:
    """SQL fragment + params restricting rows to what `viewer` may see.

    `viewer is None` means unscoped — the local CLI acting as the operator on the box.
    """
    if viewer is None or viewer.is_admin:
        return "1=1", ()
    return _VISIBLE, (viewer.user_id, viewer.user_id)


async def visible_speakers_by_name(
    conn: aiosqlite.Connection, name: str, viewer: Viewer | None
) -> list[Speaker]:
    """Every voice with this name the viewer may see, own-first.

    A list, not an Optional: two people may each share a voice called `narrator`, and the
    caller has to be able to tell that apart from an unambiguous hit.

    The own-first `ORDER BY` is belt-and-braces: no caller relies on it. All of them either
    re-derive "own" in Python or index `[0]` only behind `len(matches) == 1`, which is the
    property that keeps a duplicated name from resolving to whichever row SQLite happened to
    return. Keep it that way — do not let a new caller treat row 0 as "the right one".
    """
    where, params = _scope(viewer)
    own = viewer.user_id if viewer else None
    cur = await conn.execute(
        f"SELECT s.* FROM speakers s WHERE s.name = ? COLLATE NOCASE AND ({where})"
        " ORDER BY CASE WHEN s.owner_id IS NOT NULL AND s.owner_id = ? THEN 0 ELSE 1 END,"
        " s.name COLLATE NOCASE",
        (name, *params, own),
    )
    return [_row(r) for r in await cur.fetchall()]


async def get_speaker_by_name_and_owner(
    conn: aiosqlite.Connection, name: str, owner_id: str | None
) -> Speaker | None:
    """Exact (name, owner) lookup — the disambiguator behind `?owner=`."""
    if owner_id is None:
        cur = await conn.execute(
            "SELECT * FROM speakers WHERE name = ? COLLATE NOCASE AND owner_id IS NULL", (name,)
        )
    else:
        cur = await conn.execute(
            "SELECT * FROM speakers WHERE name = ? COLLATE NOCASE AND owner_id = ?",
            (name, owner_id),
        )
    r = await cur.fetchone()
    return _row(r) if r else None


async def get_speaker_by_id(conn: aiosqlite.Connection, speaker_id: str) -> Speaker | None:
    cur = await conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,))
    r = await cur.fetchone()
    return _row(r) if r else None


async def list_visible_with_stats(
    conn: aiosqlite.Connection, viewer: Viewer | None
) -> list[tuple[str, int, float, str, str | None, str]]:
    """(name, clips, duration_s, default_model, owner_id, visibility), name-sorted."""
    where, params = _scope(viewer)
    cur = await conn.execute(
        "SELECT s.name, COUNT(c.id) AS clips, COALESCE(SUM(c.duration_s), 0.0) AS dur,"
        " s.default_model, s.owner_id, s.visibility"
        " FROM speakers s LEFT JOIN reference_clips c ON c.speaker_id = s.id"
        f" WHERE ({where}) GROUP BY s.id ORDER BY s.name COLLATE NOCASE",
        params,
    )
    return [
        (r["name"], r["clips"], float(r["dur"]), r["default_model"], r["owner_id"], r["visibility"])
        for r in await cur.fetchall()
    ]


def refuse_inert_private(speaker: Speaker, visibility: str) -> None:
    """The update-side spelling of `refuse_unowned_private`: refuse to make an *unowned* voice
    private, because writing `private` there changes a column and nothing any caller could
    observe — reporting success would be a lie. See `UNOWNED_IS_ALWAYS_SHARED` for the whole
    rule, and `refuse_unowned_private` for the single implementation both sides call.

    Kept as its own name because `auris voices unshare` and `PATCH /v1/voices/{name}` both call
    it with a resolved `Speaker`, so the box and the service cannot drift into answering this
    differently (they did: the CLI refused locally and reported success remotely).
    """
    refuse_unowned_private(speaker.owner_id, visibility, speaker.name)


async def set_visibility(conn: aiosqlite.Connection, speaker_id: str, visibility: str) -> Speaker:
    validate_visibility(visibility)
    await conn.execute("UPDATE speakers SET visibility = ? WHERE id = ?", (visibility, speaker_id))
    # No await between the write above and this commit — see the invariant in users.set_disabled.
    await conn.commit()
    sp = await get_speaker_by_id(conn, speaker_id)
    if sp is None:
        raise LookupError(f"speaker {speaker_id!r} vanished")
    return sp


async def adopt_unowned(conn: aiosqlite.Connection, owner_id: str) -> int:
    """Assign every unowned voice to `owner_id`. Returns how many were claimed.

    Idempotent: a second run matches nothing. Visibility is left alone, and that is safe
    *because* of `UNOWNED_IS_ALWAYS_SHARED`: every row this UPDATE can match is already
    `shared`, so claiming it changes who owns the voice and not who can see it. Without that
    invariant this function would silently un-share whatever it adopted — a row stored as
    `private` is read by everyone while unowned and by nobody else once owned — which would be
    worse than making the new owner unshare it explicitly.

    Raises `NameTaken` when an unowned name is one this account already uses — the expected
    state of a box that has both legacy voices and accounts, which is exactly this function's
    audience. All-or-nothing: SQLite aborts the whole UPDATE, so nothing is claimed, and a
    re-run after the operator renames one side adopts everything.
    """
    try:
        cur = await conn.execute(
            "UPDATE speakers SET owner_id = ? WHERE owner_id IS NULL", (owner_id,)
        )
    except sqlite3.IntegrityError as exc:
        # idx_speakers_owner_name is UNIQUE per (owner, name). Rollback for the reason
        # rename_speaker documents: the aborted statement leaves the implicit transaction open,
        # and the server shares one long-lived connection, so without this a single failure
        # would hold the WAL write lock against every other writer. The names are looked up
        # *after* the rollback — for the message only, with no write outstanding.
        await conn.rollback()
        clashes = await _unowned_name_clashes(conn, owner_id)
        named = ", ".join(repr(n) for n in clashes)
        raise NameTaken(
            f"this account already has a voice named {named}" if clashes else str(exc)
        ) from exc
    count = cur.rowcount
    # No await between the write above and this commit — see the invariant in users.set_disabled.
    await conn.commit()
    return count


async def _unowned_name_clashes(conn: aiosqlite.Connection, owner_id: str) -> list[str]:
    """The unowned voice names `owner_id` already uses — what blocked `adopt_unowned`.

    NOCASE on both sides, matching the unique index that raised in the first place.
    """
    cur = await conn.execute(
        "SELECT u.name FROM speakers u WHERE u.owner_id IS NULL AND EXISTS ("
        "  SELECT 1 FROM speakers o WHERE o.owner_id = ? AND o.name = u.name COLLATE NOCASE"
        ") ORDER BY u.name COLLATE NOCASE",
        (owner_id,),
    )
    return [r["name"] for r in await cur.fetchall()]


async def delete_speaker(conn: aiosqlite.Connection, speaker_id: str) -> None:
    await conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
    await conn.commit()


async def get_clips(conn: aiosqlite.Connection, speaker_id: str) -> list[ReferenceClip]:
    cur = await conn.execute(
        "SELECT * FROM reference_clips WHERE speaker_id = ? ORDER BY created_at, id",
        (speaker_id,),
    )
    rows = await cur.fetchall()
    return [
        ReferenceClip(
            r["id"],
            r["speaker_id"],
            r["filename"],
            r["transcript"],
            r["duration_s"],
            r["created_at"],
        )
        for r in rows
    ]


async def rename_speaker(conn: aiosqlite.Connection, speaker_id: str, new_name: str) -> Speaker:
    new_name = validate_name(new_name)
    try:
        await conn.execute("UPDATE speakers SET name = ? WHERE id = ?", (new_name, speaker_id))
    except sqlite3.IntegrityError as exc:
        # idx_speakers_owner_name is UNIQUE per (owner, name); idx_speakers_unowned_name keeps
        # unowned names unique too.
        # The failed UPDATE aborts the statement but leaves the implicit transaction open, and
        # the server shares one long-lived connection — without this rollback a single 409 would
        # hold the WAL write lock against every other writer until some later request commits.
        await conn.rollback()
        raise NameTaken(f"a voice named {new_name!r} already exists") from exc
    await conn.commit()
    # By id, not by name: names are unique per owner now, so a by-name lookup here could return
    # a *different* owner's voice that happens to share the new name — the caller would then act
    # on (and, via get_clips, leak the contents of) the wrong account's voice.
    sp = await get_speaker_by_id(conn, speaker_id)
    if sp is None:  # we just committed this rename; only a concurrent delete could race it
        raise LookupError(f"speaker {speaker_id!r} vanished during rename")
    return sp


async def get_clip_by_filename(
    conn: aiosqlite.Connection, speaker_id: str, filename: str
) -> ReferenceClip | None:
    cur = await conn.execute(
        "SELECT * FROM reference_clips WHERE speaker_id = ? AND filename = ?",
        (speaker_id, filename),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    return ReferenceClip(
        r["id"], r["speaker_id"], r["filename"], r["transcript"], r["duration_s"], r["created_at"]
    )


async def delete_clip_if_not_last(
    conn: aiosqlite.Connection, speaker_id: str, clip_id: str
) -> bool:
    """Delete a clip unless it is the speaker's only one. False means it was the last.

    The count and the delete are one statement on purpose. Split across an await they are a
    TOCTOU: the server shares a single connection, so two concurrent deletes would both read
    "2 clips", both proceed, and strip the voice to zero — the exact state the guard exists to
    prevent, and one a voice cannot synthesise from.
    """
    cur = await conn.execute(
        "DELETE FROM reference_clips WHERE id = ? AND speaker_id = ? "
        "AND (SELECT COUNT(*) FROM reference_clips WHERE speaker_id = ?) > 1",
        (clip_id, speaker_id, speaker_id),
    )
    await conn.commit()
    return cur.rowcount > 0
