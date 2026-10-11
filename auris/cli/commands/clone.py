from __future__ import annotations

import asyncio
from pathlib import Path

import soundfile as sf
import typer

from auris.audio import io, reference
from auris.audio.transcribe import transcribe_reference
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.voice_library import cloning, db, speakers

MIN_CLIP_SECONDS = 3.0


async def _clone(
    name: str, ref: Path, ref_text: str | None, asr_model: str | None = None
) -> tuple[str, int]:
    settings = load_settings()
    audio, sr = io.load_audio(ref)
    audio = io.resample(audio, sr, settings.sample_rate)
    if io.duration_s(audio, settings.sample_rate) < MIN_CLIP_SECONDS:
        seconds = io.duration_s(audio, settings.sample_rate)
        raise ValueError(
            f"reference audio must be at least {MIN_CLIP_SECONDS:.0f} seconds (got {seconds:.1f}s)"
        )

    decoded = None
    if ref_text is None:
        window = reference.decode_window(audio, settings.sample_rate)
        decoded = transcribe_reference(window, settings.sample_rate, model=asr_model)
    # Trim + cap to ≤10 s, cut inside a pause (see auris/audio/reference.py for why).
    clip = reference.reference_for_clone(
        audio, settings.sample_rate, ref_text=ref_text, decoded=decoded
    )
    audio, ref_text = clip.audio, clip.transcript
    seconds = io.duration_s(audio, settings.sample_rate)
    for warning in clip.warnings:
        typer.echo(f"note: {warning}", err=True)

    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        # Unscoped-owner lookup, not the global by-name one: the operator's own (unowned)
        # namespace, matching what the server uses for a master-token clone. A global lookup
        # here would append the operator's clip to a different account's private voice.
        # Deliberately with no "else, the single voice anywhere with this name" fallback, which
        # `auris tts` and `auris voices share` both have: a read may reach a colleague's voice
        # (the operator is admin on the box) and so may a visibility flip, which is reversible
        # and visible in the listing — but appending audio into someone's voice silently
        # changes what it sounds like, so it stays confined to the operator's own namespace.
        speaker = await speakers.get_speaker_by_name_and_owner(conn, name, None)
        if speaker is None:
            raise LookupError(f"speaker {name!r} not found — run 'auris speakers add' first")

        index = await cloning.add_reference(
            conn, settings.voices_dir, speaker.id, audio, settings.sample_rate, ref_text, seconds
        )
        return speaker.name, index
    finally:
        await conn.close()


def clone(
    speaker: str = typer.Argument(..., help="Speaker name (must already exist)"),
    ref: Path = typer.Option(..., "--ref", exists=True, help="Reference audio file"),
    ref_text: str = typer.Option(
        None, "--ref-text", help="Transcript of the reference (auto-transcribed if omitted)"
    ),
    asr_model: str = typer.Option(
        None, "--asr-model", help="Speech-to-text model to transcribe with (see 'auris asr list')"
    ),
) -> None:
    """Clone a voice from a reference clip. Transcribes the audio if --ref-text is omitted."""
    sc = remote_client()
    if sc is not None:  # remote mode: upload the file; the service trims/transcribes/stores
        try:
            res = sc.clone(speaker, ref.read_bytes(), ref.name, ref_text, asr_model)
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(f'Cloned {res["voice"]}: clip {res["clip_index"]} ("{res["transcript"]}")')
        for warning in res.get("warnings", []):
            typer.echo(f"note: {warning}", err=True)
        return
    try:
        name, index = asyncio.run(_clone(speaker, ref, ref_text, asr_model))
    except (ValueError, LookupError, sf.SoundFileError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Cloned {name}: stored clip_{index:03d}.wav")
