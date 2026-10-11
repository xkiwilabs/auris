from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from auris.audio import io


def clips_dir(voices_dir: str | Path, speaker_id: str) -> Path:
    return Path(voices_dir) / speaker_id / "clips"


def next_clip_index(voices_dir: str | Path, speaker_id: str) -> int:
    d = clips_dir(voices_dir, speaker_id)
    if not d.exists():
        return 1
    existing = [int(p.stem.split("_")[1]) for p in d.glob("clip_*.wav")]
    return max(existing, default=0) + 1


def store_clip(
    voices_dir: str | Path,
    speaker_id: str,
    audio: np.ndarray,
    sample_rate: int,
    index: int,
) -> Path:
    path = clips_dir(voices_dir, speaker_id) / f"clip_{index:03d}.wav"
    io.save_wav(path, audio, sample_rate)
    return path


def clip_index(filename: str) -> int:
    """'clip_003.wav' -> 3. The index is what users see (`clip_003.wav` in the CLI)."""
    m = re.fullmatch(r"clip_(\d+)\.wav", filename)
    if m is None:
        raise ValueError(f"not a clip filename: {filename!r}")
    return int(m.group(1))


def remove_clip(voices_dir: str | Path, speaker_id: str, filename: str) -> None:
    path = clips_dir(voices_dir, speaker_id) / filename
    try:
        path.unlink()
    except FileNotFoundError:
        pass  # already gone; the DB row is the source of truth
