from __future__ import annotations

import asyncio
from typing import NamedTuple

from auris.audio import io
from auris.config import Settings
from auris.models.base import PreparedReference
from auris.voice_library import db, speakers, storage
from auris.voice_library.exceptions import AmbiguousVoice


class SpeakerReference(NamedTuple):
    reference: PreparedReference
    default_model: str


async def load_reference_for_speaker(
    conn, settings: Settings, speaker: speakers.Speaker
) -> SpeakerReference:
    """The latest clip + transcript for an already-resolved speaker.

    Split out of `load_reference` so a caller that has already found the right row — the scoped
    `resolve_voice` — does not look the name up a second time, which would bypass the scoping.
    """
    clips = await speakers.get_clips(conn, speaker.id)
    if not clips:
        raise LookupError(
            f"speaker {speaker.name!r} has no reference clip — run 'auris clone' first"
        )
    clip = clips[-1]
    clip_path = storage.clips_dir(settings.voices_dir, speaker.id) / clip.filename
    ref_audio, ref_sr = await asyncio.to_thread(io.load_audio, clip_path)
    reference = PreparedReference(audio=ref_audio, sample_rate=ref_sr, transcript=clip.transcript)
    return SpeakerReference(reference, speaker.default_model)


async def load_reference(conn, settings: Settings, name: str) -> SpeakerReference:
    """Unscoped lookup by name, for the local CLI with no `--owner` given.

    The operator has no account of their own, so an *unowned* voice with this name is the
    operator's own namespace and wins outright — exactly how `cloning.get_or_create_speaker`
    resolves `auris clone`'s target, so the two verbs agree on which voice a bare name means on
    the same box (a `narrator` that `auris tts` picked up as a colleague's, while `auris clone`
    was quietly filling the operator's own `narrator`, was a determinism defect, not a feature).

    Only when nothing unowned matches do we fall back to "is there exactly one voice anywhere
    with this name" — the case that keeps a box with no accounts (every voice unowned) and a
    uniquely-named voice working without ceremony. Two or more owners holding the name is
    `AmbiguousVoice` rather than a silent pick of whichever row SQLite happens to return first.
    """
    speaker = await speakers.get_speaker_by_name_and_owner(conn, name, None)
    if speaker is None:
        matches = await speakers.visible_speakers_by_name(conn, name, None)
        if not matches:
            # "voice", not "speaker": the same wording as resolve_voice and `auris voices`, for
            # the same object. (`auris clone` still says "speaker" — it names the `auris
            # speakers add` command the operator has to run.)
            raise LookupError(f"voice {name!r} not found")
        if len(matches) == 1:
            speaker = matches[0]
        else:
            # Reached only from the local CLI's unscoped path: every HTTP caller supplies a
            # real Viewer (even the master token — see `resolve_voice`'s `viewer is None`
            # note), so this message can name the CLI's own `--owner` flag directly rather
            # than raising a surface-neutral fact for some other caller to re-phrase.
            raise AmbiguousVoice(
                f"{len(matches)} voices are named {name!r}; pass --owner <email> to choose one",
                owners=tuple(s.owner_id for s in matches),
            )
    return await load_reference_for_speaker(conn, settings, speaker)


async def load_speaker_reference(settings: Settings, name: str) -> SpeakerReference:
    """Open a short-lived connection and delegate to load_reference (CLI path, unchanged)."""
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        return await load_reference(conn, settings, name)
    finally:
        await conn.close()
