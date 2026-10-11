from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

_FFMPEG_TIMEOUT_S = 120  # generous cap so a malformed input can't hang the decode


def _ffmpeg_tool(name: str) -> str:
    """Return the absolute path to an FFmpeg binary (ffmpeg/ffprobe) or raise.

    FFmpeg is already a runtime dependency (torchcodec needs libavutil); we reuse it
    to decode formats libsndfile can't (mp3, m4a/AAC, mp4 audio tracks)."""
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(
            f"{name} not found — install FFmpeg to load this format "
            "(macOS: brew install ffmpeg, Debian/Ubuntu: apt install ffmpeg)"
        )
    return path


def _decode_with_ffmpeg(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode any FFmpeg-readable file to (float32 mono PCM, sample_rate).

    Probes the source sample rate with ffprobe (preserving it for the existing resample
    step), then has FFmpeg decode the audio stream to raw mono f32le on stdout. `-vn`
    drops any video stream, so a video container (e.g. mp4) yields just its audio."""
    ffprobe = _ffmpeg_tool("ffprobe")
    ffmpeg = _ffmpeg_tool("ffmpeg")

    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=_FFMPEG_TIMEOUT_S,
    )
    rate = probe.stdout.strip().splitlines()[0] if probe.stdout.strip() else ""
    if probe.returncode != 0 or not rate:
        detail = probe.stderr.strip()[-200:] or "no audio stream found"
        raise RuntimeError(f"could not read audio from {path}: {detail}")
    sample_rate = int(rate)

    decode = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "pipe:1",
        ],
        capture_output=True,
        timeout=_FFMPEG_TIMEOUT_S,
    )
    if decode.returncode != 0:
        detail = decode.stderr.decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"ffmpeg failed to decode {path}: {detail}")

    audio = np.frombuffer(decode.stdout, dtype=np.float32)
    if audio.size == 0:
        raise RuntimeError(f"no audio decoded from {path}")
    return np.array(audio, dtype=np.float32), sample_rate  # copy: frombuffer view is read-only


def load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Load audio as float32 mono. Returns (audio, sample_rate).

    Tries libsndfile (soundfile) first for wav/flac/ogg; falls back to FFmpeg for
    formats it can't decode (mp3, m4a/AAC, mp4 audio tracks, ...)."""
    try:
        audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    except sf.SoundFileError as exc:
        # Re-raise OS-level open failures instead of routing them to FFmpeg, so callers
        # keep a clean error. libsndfile code 2 = SF_ERR_SYSTEM: a missing file, a
        # permission-denied file, etc. (Re-raising a perms error rather than feeding it
        # to FFmpeg is the correct behavior too.) A real decode/format failure raises a
        # different code (e.g. code 1, format not recognised) and falls through to FFmpeg.
        if getattr(exc, "code", None) == 2:  # SF_ERR_SYSTEM: OS-level open failure
            raise
        audio, sr = _decode_with_ffmpeg(path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), int(sr)


def resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample float32 audio from orig_sr to target_sr (both in Hz). Returns float32.

    No-op (returns a float32 copy) when the rates are equal.
    """
    if orig_sr == target_sr:
        return audio.astype(np.float32)
    out = librosa.resample(audio.astype(np.float32), orig_sr=orig_sr, target_sr=target_sr)
    return out.astype(np.float32)


def save_wav(path: str | Path, audio: np.ndarray, sample_rate: int) -> Path:
    """Write mono float32 audio to a 16-bit PCM WAV file, creating parent dirs.

    16-bit PCM is the standard on-disk format for speech and is used deliberately
    for both reference clips and generated output. Returns the written path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.astype(np.float32), sample_rate, subtype="PCM_16")
    return path


def duration_s(audio: np.ndarray, sample_rate: int) -> float:
    return len(audio) / sample_rate
