from __future__ import annotations

from typing import NamedTuple

from auris.config import Settings
from auris.models.base import Conditioning, PresetVoice
from auris.models.presets import PRESETS, PRESETS_BY_NAME
from auris.voice_library import blends, references, speakers
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.viewer import Viewer


class Resolved(NamedTuple):
    model: str
    conditioning: Conditioning
    kind: str  # "preset" | "cloned" | "blend"


_PRESET_NAMES_CI = {n.casefold() for n in PRESETS_BY_NAME}


def is_preset(name: str) -> bool:
    """Whether a name is reserved by a built-in preset.

    Strips and casefolds, because this is the *reservation* check: the speakers index is
    UNIQUE COLLATE NOCASE and callers may pass an untrimmed name, so `" Narrator-US-F "` has to
    be refused exactly as `narrator-us-f` is. Otherwise a cloned voice could be stored under a
    preset's name, where `resolve_voice` — which matches presets first, exactly — would shadow
    it and silently synthesise the preset instead.
    """
    return name.strip().casefold() in _PRESET_NAMES_CI


def preset_voice_rows() -> list[dict]:
    """VoiceInfo-shaped rows for the built-in presets (no DB, no model load)."""
    return [
        {
            "name": p.name,
            "clips": 0,
            "duration_s": 0.0,
            "default_model": "kokoro",
            "kind": "preset",
            "description": p.desc,
        }
        for p in PRESETS
    ]


async def describe_ambiguity(conn, name: str, owners: tuple[str | None, ...]) -> str:
    """The *local* CLI's ambiguity message, naming the accounts that hold `name`.

    Shared by `resolve_voice_standalone` (`auris tts`/`auris say`) and `auris voices
    share|unshare`, so the two local paths ask for a disambiguator in the same words and from
    the same code.

    **Local only.** Over HTTP the message deliberately carries a bare count instead (see
    `voice_access.get_for_read` and `resolve_voice` below): a remote caller must not learn who
    else holds a name. On the box the operator is already admin over every row, and a count
    alone is a dead end — it asks for an email it does not supply. Do not route an HTTP error
    through here.
    """
    from auris.voice_library import users

    emails = await users.emails_by_id(conn, {o for o in owners if o})
    # "(unowned)" is defensive rather than reachable: both callers take the unowned namespace
    # first and only fall through once every match is owned. It keeps a None in `owners` from
    # crashing sorted() on a mixed list if some third caller ever appears.
    owned_by = ", ".join(sorted(emails.get(o, o) if o else "(unowned)" for o in owners))
    return (
        f"{len(owners)} voices are named {name!r}, owned by: {owned_by}; "
        "pass --owner <email> to choose one"
    )


async def resolve_voice(
    conn,
    settings: Settings,
    name: str,
    *,
    viewer: Viewer | None = None,
    owner: str | None = None,
) -> Resolved:
    """Map a voice name to (model, conditioning).

    Presets win the namespace, exactly as before. `viewer=None` is the unscoped *operator* path
    the local CLI passes: scoping is off entirely, regardless of `owner` — there, `owner` is a
    plain disambiguator, not a security boundary (Task 7's `auris voices --owner <email>` relies
    on exactly this, so don't "fix" this branch into requiring a viewer).

    Every HTTP path supplies a `viewer`. There, resolution is preset → the viewer's own match →
    shared matches: if the viewer owns one of the matches, that one wins outright, so sharing a
    voice under someone else's name can never shadow — or deny service to — their own voice.
    Only when no single match is the viewer's own, and more than one is visible, is it
    `AmbiguousVoice`. `owner` (a user id) is resolved within the viewer's visible set, so it can
    never reach a hidden voice.
    """
    if name in PRESETS_BY_NAME:
        p = PRESETS_BY_NAME[name]
        return Resolved("kokoro", PresetVoice(p.voice_id, p.lang_code), "preset")
    if viewer is None:
        if owner is not None:
            speaker = await speakers.get_speaker_by_name_and_owner(conn, name, owner)
            if speaker is None:
                raise LookupError(f"voice {name!r} not found")
        else:
            speaker = await references.find_unscoped(conn, name)
        return await _resolve_row(conn, settings, speaker)
    matches = await speakers.visible_speakers_by_name(conn, name, viewer)
    if owner is not None:
        matches = [s for s in matches if s.owner_id == owner]
    if not matches:
        raise LookupError(f"voice {name!r} not found")
    own = [s for s in matches if viewer.user_id is not None and s.owner_id == viewer.user_id]
    # idx_speakers_owner_name is UNIQUE on (owner_id, name), so the viewer can own at most one
    # of these matches — but that's an invariant of a different table's index, not of this
    # function, so check len(own) rather than assume it: if the invariant were ever broken,
    # this falls through to AmbiguousVoice instead of silently picking one.
    if len(own) == 1:
        chosen = own[0]
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        owners = tuple(s.owner_id for s in matches)
        # A bare count, not the owner ids: this message is reachable over HTTP via the three
        # synthesis routes, so it must not leak who else has a voice by this name. `owner=`
        # names the query/body field the HTTP surface actually accepts (an email, resolved to a
        # user id before reaching this function) — `owners` still carries the ids on the
        # exception itself, for a programmatic caller, never for display. Keep this in step
        # with voice_access.get_for_read's wording; they must not drift apart again.
        raise AmbiguousVoice(
            f"several voices named {name!r} are visible to you ({len(matches)}); "
            "pass owner=<email> to choose one",
            owners=owners,
        )
    return await _resolve_row(conn, settings, chosen)


async def _resolve_row(conn, settings: Settings, speaker: speakers.Speaker) -> Resolved:
    """A found voice row → what synthesises it: a blend's mixed Kokoro voice, or a cloned
    voice's latest reference clip."""
    if speaker.is_blend:
        return Resolved("kokoro", blends.from_json(speaker.blend).conditioning(), "blend")
    ref = await references.load_reference_for_speaker(conn, settings, speaker)
    return Resolved(ref.default_model, ref.reference, "cloned")


async def resolve_voice_standalone(
    settings: Settings, name: str, owner: str | None = None
) -> Resolved:
    """resolve_voice for callers without a connection (`auris tts`/`auris say`). Presets need no
    DB; cloned voices open a short-lived connection.

    `owner`, if given, is an email — resolved to a user id *here, inside `voice_library`*,
    never via `server/voice_access.owner_id_for`: `auris/cli/` must never import anything that
    pulls in `fastapi` (see `tests/cli/test_import_seam.py`). An unknown address raises
    `LookupError` via `users.get_user_id_or_raise`, the same wording every other local CLI
    `--owner` flag uses.
    """
    from auris.voice_library import db, users

    if name in PRESETS_BY_NAME:
        p = PRESETS_BY_NAME[name]
        return Resolved("kokoro", PresetVoice(p.voice_id, p.lang_code), "preset")
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        owner_id = await users.get_user_id_or_raise(conn, owner) if owner is not None else None
        try:
            return await resolve_voice(conn, settings, name, owner=owner_id)
        except AmbiguousVoice as exc:
            # Re-rendered with the owning emails for the same reason the email is resolved
            # here: this is the local CLI's entry point and nothing else, so it can say more
            # than the surface-neutral message `load_reference` had to raise (which must stay
            # safe for any caller). See `describe_ambiguity`.
            raise AmbiguousVoice(
                await describe_ambiguity(conn, name, exc.owners), owners=exc.owners
            ) from exc
    finally:
        await conn.close()
