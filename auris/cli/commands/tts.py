from __future__ import annotations

import asyncio
import time
from pathlib import Path

import soundfile as sf
import typer

from auris.audio import io
from auris.cli.timing import format_timing
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.models import build_model
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.resolve import resolve_voice_standalone


async def _tts(speaker_name: str, text: str, out: Path, speed: float, owner: str | None) -> Path:
    settings = load_settings()
    r = await resolve_voice_standalone(settings, speaker_name, owner=owner)
    model = build_model(r.model, settings)
    audio = await asyncio.to_thread(model.synthesise, text, r.conditioning, speed)
    return io.save_wav(out, audio, settings.sample_rate)


def tts(
    speaker: str = typer.Argument(..., help="Speaker or preset voice name"),
    text: str = typer.Option(..., "--text", help="Text to synthesise"),
    out: Path = typer.Option(..., "--out", help="Output WAV path"),
    speed: float = typer.Option(1.0, "--speed", min=0.5, max=2.0),
    owner: str | None = typer.Option(None, "--owner", help="Disambiguate a duplicated name"),
) -> None:
    """Synthesise speech from text in a cloned or built-in preset voice."""
    sc = remote_client()
    if sc is not None:  # remote mode: the service synthesises, we just save the bytes
        fmt = out.suffix.lstrip(".").lower() or "mp3"
        t0 = time.perf_counter()
        try:
            out.write_bytes(sc.tts(speaker, text, speed, fmt, owner=owner))
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Wrote {out}  ({format_timing(time.perf_counter() - t0, text)})")
        return
    t0 = time.perf_counter()
    try:
        path = asyncio.run(_tts(speaker, text, out, speed, owner))
    except (LookupError, AmbiguousVoice, sf.SoundFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Wrote {path}  ({format_timing(time.perf_counter() - t0, text)})")
