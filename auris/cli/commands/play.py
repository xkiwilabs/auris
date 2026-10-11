from __future__ import annotations

from pathlib import Path

import soundfile as sf
import typer

from auris.audio import io
from auris.audio.playback import play_audio


def play(
    file: Path = typer.Argument(..., exists=True, help="WAV file to play"),
) -> None:
    """Play an audio file through your speakers (cross-platform)."""
    try:
        audio, sample_rate = io.load_audio(file)
        play_audio(audio, sample_rate)
    except (RuntimeError, sf.SoundFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
