from __future__ import annotations

import io
import subprocess

import numpy as np
import soundfile as sf

from auris.audio.io import _ffmpeg_tool

_CONTENT_TYPES = {
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
}


def content_type(fmt: str) -> str:
    try:
        return _CONTENT_TYPES[fmt.lower()]
    except KeyError:
        raise ValueError(f"unsupported audio format: {fmt!r}") from None


def encode_audio(audio: np.ndarray, sample_rate: int, fmt: str) -> tuple[bytes, str]:
    """Encode mono float32 audio to (bytes, content-type) for an HTTP response.

    pcm = headerless signed-16-bit little-endian (best for streaming / realtime clients);
    wav = 16-bit PCM WAV; mp3/opus via FFmpeg (already a runtime dependency)."""
    fmt = fmt.lower()
    audio = np.asarray(audio, dtype=np.float32)
    if fmt == "pcm":
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return pcm16, _CONTENT_TYPES["pcm"]
    if fmt == "wav":
        buf = io.BytesIO()
        sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
        return buf.getvalue(), _CONTENT_TYPES["wav"]
    if fmt in ("mp3", "opus"):
        return _encode_ffmpeg(audio, sample_rate, fmt), _CONTENT_TYPES[fmt]
    raise ValueError(f"unsupported audio format: {fmt!r}")


def _encode_ffmpeg(audio: np.ndarray, sample_rate: int, fmt: str) -> bytes:
    ffmpeg = _ffmpeg_tool("ffmpeg")
    codec = {"mp3": ("-f", "mp3"), "opus": ("-f", "ogg", "-c:a", "libopus")}[fmt]
    proc = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            *codec,
            "pipe:1",
        ],
        input=audio.tobytes(),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"ffmpeg failed to encode {fmt}: {detail}")
    return proc.stdout
