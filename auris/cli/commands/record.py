from __future__ import annotations

import asyncio
import io as _io

import numpy as np
import soundfile as sf
import typer

from auris.audio import io, reference
from auris.audio.record import record_audio
from auris.audio.transcribe import transcribe_reference
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import Settings, load_settings
from auris.voice_library import cloning, db, speakers

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


async def _refuse_blend(settings: Settings, name: str) -> None:
    """ValueError if `name` is a blended voice in the operator's own namespace — the one
    `_persist` would append to — so nobody records 12 seconds only to have them refused."""
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        existing = await speakers.get_speaker_by_name_and_owner(conn, name, None)
        if existing is not None:
            await speakers.refuse_if_blend(conn, existing.id)
    finally:
        await conn.close()


def record(
    speaker: str = typer.Argument(..., help="Speaker name (created if it doesn't exist)"),
    seconds: float = typer.Option(
        12.0,
        "--seconds",
        min=3.0,
        max=30.0,
        help="How long to record. The clone keeps up to 10 s, ending at a pause, so leave a "
        "little spare: a recording that stops mid-word loses that word",
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
    if remote_client() is None:  # the service refuses this itself, before any decode
        try:
            asyncio.run(_refuse_blend(settings, speaker))
        except ValueError as exc:  # don't ask for a recording that can't be stored
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    if free:
        typer.echo(f"Free-form mode — speak naturally for {seconds:.0f}s. Say whatever you like.\n")
    else:
        typer.echo("Read this passage aloud, clearly and at a natural pace:\n")
        typer.echo(f"    {passage}\n")
    typer.echo(
        f"Recording {seconds:.0f}s from your default microphone. Speak now — it's fine to "
        "finish early; the clone keeps up to 10 s, ending at a pause."
    )

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
        for warning in res.get("warnings", []):
            typer.echo(f"note: {warning}", err=True)
        return

    typer.echo("Transcribing…")
    try:
        window = reference.decode_window(audio, settings.sample_rate)
        decoded = transcribe_reference(window, settings.sample_rate, model=asr_model)
        # Trim + cap to ≤10 s, cut inside a pause (see auris/audio/reference.py for why).
        clip = reference.reference_for_clone(
            audio, settings.sample_rate, ref_text=None, decoded=decoded
        )
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    for warning in clip.warnings:
        typer.echo(f"note: {warning}", err=True)
    audio, transcript = clip.audio, clip.transcript

    try:
        name, index = asyncio.run(_persist(settings, speaker, audio, transcript))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Recorded and cloned {name}: stored clip_{index:03d}.wav")
