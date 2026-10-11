from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import typer

from auris.audio import io
from auris.audio.playback import play_stream
from auris.audio.stream import stream_synthesis
from auris.cli.timing import format_timing
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.models import build_model
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.resolve import resolve_voice_standalone


def _say_remote(sc, voice: str, content: str, speed: float, out, owner: str | None = None) -> None:
    """Stream PCM from the service and play it live (optionally save the full WAV)."""
    rate = 24000
    saved: list[np.ndarray] = []

    def _chunks():
        leftover = b""
        for chunk in sc.stream(voice, content, speed, owner=owner):
            buf = leftover + chunk
            n = len(buf) - (len(buf) % 2)
            i16 = np.frombuffer(buf[:n], dtype="<i2")
            leftover = buf[n:]
            if len(i16):
                arr = (i16.astype(np.float32) / 32768.0).copy()
                saved.append(arr)
                yield arr

    t0 = time.perf_counter()
    try:
        play_stream(_chunks(), rate)
    except (ClientError, RuntimeError, sf.SoundFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Done · {format_timing(time.perf_counter() - t0, content)} (incl. playback)")
    if out is not None and saved:
        io.save_wav(out, np.concatenate(saved), rate)
        typer.echo(f"Saved {out}")


def say(
    speaker: str = typer.Argument(..., help="Speaker or preset voice name"),
    text: str = typer.Option(None, "--text", help="Text to speak (or use --file)"),
    file: Path = typer.Option(None, "--file", exists=True, help="Text file to speak"),
    out: Path = typer.Option(None, "--out", help="Optional: also save the full audio here"),
    speed: float = typer.Option(1.0, "--speed", min=0.5, max=2.0),
    owner: str | None = typer.Option(None, "--owner", help="Disambiguate a duplicated name"),
) -> None:
    """Stream speech in a cloned or built-in preset voice, playing it as it generates.

    With neither --text nor --file, speaks a short sample so you can quickly test the voice."""
    if text is not None and file is not None:
        typer.echo("provide at most one of --text or --file", err=True)
        raise typer.Exit(code=2)
    if text is not None:
        content = text
    elif file is not None:
        content = file.read_text()
    else:
        content = "This is a test of this voice. How does it sound to you?"
    if not content.strip():
        typer.echo("no text to synthesise", err=True)
        raise typer.Exit(code=1)

    sc = remote_client()
    if sc is not None:  # remote mode: stream from the service and play live
        _say_remote(sc, speaker, content, speed, out, owner=owner)
        return

    settings = load_settings()
    try:
        resolved = asyncio.run(resolve_voice_standalone(settings, speaker, owner=owner))
        reference = resolved.conditioning
        model = build_model(resolved.model, settings)
    except (LookupError, ValueError, AmbiguousVoice, sf.SoundFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    saved: list[np.ndarray] = []

    def _chunks():
        for chunk in stream_synthesis(model, content, reference, speed):
            saved.append(chunk)
            yield chunk

    t0 = time.perf_counter()
    try:
        play_stream(_chunks(), settings.sample_rate)
    except (RuntimeError, sf.SoundFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except Exception as exc:  # any other synthesis failure → clean error, not a traceback
        typer.echo(f"synthesis failed: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Done · {format_timing(time.perf_counter() - t0, content)} (incl. playback)")

    if out is not None and saved:
        io.save_wav(out, np.concatenate(saved), settings.sample_rate)
        typer.echo(f"Saved {out}")
    elif out is not None:
        typer.echo("nothing was generated; --out not written", err=True)
