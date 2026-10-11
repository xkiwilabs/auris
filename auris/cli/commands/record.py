from __future__ import annotations

import asyncio
import io as _io

import numpy as np
import soundfile as sf
import typer

from auris.audio import io
from auris.audio.record import record_audio
from auris.audio.transcribe import transcribe_audio
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import Settings, load_settings
from auris.voice_library import cloning, db

# Default passage to read — phonetically rich for good coverage. Shown as guidance only:
# the transcript now comes from transcribing the actual recording, so you can say anything.
RAINBOW_PASSAGE = (
    "When the sunlight strikes raindrops in the air, they act as a prism and form a "
    "rainbow. The rainbow is a division of white light into many beautiful colors."
)


async def _persist(
    settings: Settings, name: str, audio: np.ndarray, transcript: str
) -> tuple[str, int]:
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        speaker = await cloning.get_or_create_speaker(conn, name)
        index = await cloning.add_reference(
            conn,
            settings.voices_dir,
            speaker.id,
            audio,
            settings.sample_rate,
            transcript,
            io.duration_s(audio, settings.sample_rate),
        )
        return speaker.name, index
    finally:
        await conn.close()


def record(
    speaker: str = typer.Argument(..., help="Speaker name (created if it doesn't exist)"),
    seconds: float = typer.Option(
        10.0,
        "--seconds",
        min=3.0,
        max=10.0,
        help="Recording length in seconds (max 10 — f5-TTS caps references at ~10s)",
    ),
    passage: str = typer.Option(
        RAINBOW_PASSAGE, "--passage", help="Text to read aloud (guidance only)"
    ),
    free: bool = typer.Option(
        False, "--free", help="Free-form: don't show a passage, just say anything"
    ),
    asr_model: str = typer.Option(
        None, "--asr-model", help="Speech-to-text model to transcribe with (see 'auris asr list')"
    ),
) -> None:
    """Record yourself speaking and clone your voice in one step.

    The recording is transcribed automatically, so the stored transcript always matches
    what you actually said — read the passage, or pass --free and say anything."""
    settings = load_settings()
    if free:
        typer.echo(f"Free-form mode — speak naturally for {seconds:.0f}s. Say whatever you like.\n")
    else:
        typer.echo("Read this passage aloud, clearly and at a natural pace:\n")
        typer.echo(f"    {passage}\n")
    typer.echo(f"Recording {seconds:.0f}s from your default microphone. Speak now!")

    def _tick(remaining: int) -> None:
        # Overwrite the same line each second for a live countdown.
        typer.echo(f"\r  ● recording… {remaining:2d}s left ", nl=False)

    try:
        audio = record_audio(seconds, settings.sample_rate, on_tick=_tick)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo("")  # finish the live countdown line

    sc = remote_client()
    if sc is not None:  # remote mode: upload the recording; the service trims/transcribes/stores
        buf = _io.BytesIO()
        sf.write(buf, audio, settings.sample_rate, format="WAV", subtype="PCM_16")
        typer.echo("Uploading…")
        try:
            res = sc.clone(speaker, buf.getvalue(), "recording.wav", asr_model=asr_model)
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(
            f"Recorded and uploaded {res['voice']}: clip {res['clip_index']} "
            f'("{res["transcript"]}")'
        )
        return

    audio = io.prepare_reference(
        audio, settings.sample_rate
    )  # trim + cap to ≤10s (safely under f5-TTS's ~12s limit)
    typer.echo("Transcribing…")
    try:
        transcript = transcribe_audio(audio, settings.sample_rate, model=asr_model)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    if not transcript.strip():
        typer.echo("no speech detected — please try again", err=True)
        raise typer.Exit(code=1)

    name, index = asyncio.run(_persist(settings, speaker, audio, transcript))
    typer.echo(f"Recorded and cloned {name}: stored clip_{index:03d}.wav")
