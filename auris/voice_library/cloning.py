from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import numpy as np

from auris.voice_library import speakers, storage


async def get_or_create_speaker(
    conn: aiosqlite.Connection,
    name: str,
    default_model: str = "f5_tts",
    owner_id: str | None = None,
) -> speakers.Speaker:
    """The caller's speaker with this name, creating it if absent.

    Scoped to `owner_id`: the name namespace is per-owner, so a global lookup here would append
    the caller's clip to a different account's voice. Atomic against concurrent writers via the
    UNIQUE (owner_id, name) index — a racing duplicate INSERT raises IntegrityError and we
    re-read the winner.
    """
    speaker = await speakers.get_speaker_by_name_and_owner(conn, name, owner_id)
    if speaker is not None:
        return speaker
    try:
        return await speakers.create_speaker(conn, name, default_model, owner_id=owner_id)
    except sqlite3.IntegrityError:
        await conn.rollback()
        winner = await speakers.get_speaker_by_name_and_owner(conn, name, owner_id)
        if winner is None:
            raise
        return winner


async def add_reference(
    conn: aiosqlite.Connection,
    voices_dir: str | Path,
    speaker_id: str,
    audio: np.ndarray,
    sample_rate: int,
    transcript: str,
    duration_s: float,
) -> int:
    """Persist a reference clip (audio file + db row with transcript) for a speaker.

    Returns the clip index. Shared by the `clone` (file) and `record` (mic) commands."""
    await speakers.refuse_if_blend(conn, speaker_id)  # before the WAV is written, not after
    index = storage.next_clip_index(voices_dir, speaker_id)
    path = storage.store_clip(voices_dir, speaker_id, audio, sample_rate, index)
    await speakers.add_clip(
        conn,
        speaker_id=speaker_id,
        filename=path.name,
        transcript=transcript,
        duration_s=duration_s,
    )
    return index
