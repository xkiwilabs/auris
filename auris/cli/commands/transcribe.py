from __future__ import annotations

import io as _io
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import typer

from auris.audio import io
from auris.audio.asr_models import resolve_asr_model
from auris.audio.record import record_audio
from auris.audio.transcribe import transcribe_detailed
from auris.audio.transcript import TranscribeOptions
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings
from auris.server.transcript_format import FORMATS, render

# The CLI offers exactly what the service renders, so `--format` and the HTTP
# `response_format` never drift apart. text/srt/vtt print as-is; the JSON ones are indented.
CLI_FORMATS = FORMATS


def _resolve_language(language: str | None) -> str | None:
    """Return None to auto-detect. The default and "auto" detect; anything else forces
    that language code."""
    if language is None or language.lower() == "auto":
        return None
    return language


def _record(seconds: float, sample_rate: int) -> np.ndarray:
    """Record from the mic, narrating on stderr so a piped transcript stays clean.

    Deliberately uncapped: the ≤10 s limit elsewhere is f5-TTS's reference constraint,
    not a transcription one."""
    typer.echo(f"Recording {seconds:.0f}s from your default microphone. Speak now!", err=True)

    def _tick(remaining: int) -> None:
        typer.echo(f"\r  ● recording… {remaining:2d}s left ", nl=False, err=True)

    audio = record_audio(seconds, sample_rate, on_tick=_tick)
    typer.echo("", err=True)  # finish the live countdown line
    return audio


def _wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    buf = _io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def transcribe(
    audio_file: Path = typer.Argument(
        None, exists=True, help="Audio file to transcribe (any format ffmpeg can read)"
    ),
    out: Path = typer.Option(None, "--out", help="Write to this file instead of stdout"),
    fmt: str = typer.Option("text", "--format", help=f"Output format: {', '.join(CLI_FORMATS)}"),
    asr_model: str = typer.Option(
        None, "--asr-model", help="Speech-to-text model (see 'auris asr list')"
    ),
    language: str = typer.Option(
        None, "--language", help="Force a language code, or 'auto' to detect (default: auto)"
    ),
    translate: bool = typer.Option(
        False, "--translate", help="Translate into English instead of transcribing verbatim"
    ),
    word_timestamps: bool = typer.Option(
        False, "--word-timestamps", help="Include per-word timings (verbose_json only)"
    ),
    prompt: str = typer.Option(
        None, "--prompt", help="Bias the spelling of names/jargon, e.g. 'auris, f5-TTS'"
    ),
    mic: bool = typer.Option(False, "--mic", help="Record from the microphone instead"),
    seconds: float = typer.Option(30.0, "--seconds", min=1.0, help="Recording length for --mic"),
) -> None:
    """Transcribe speech to text. Reads a file, or records from the mic with --mic."""
    if fmt not in CLI_FORMATS:
        typer.echo(f"--format must be one of {', '.join(CLI_FORMATS)}", err=True)
        raise typer.Exit(code=1)
    if audio_file is None and not mic:
        typer.echo("give an audio file to transcribe, or pass --mic to record one", err=True)
        raise typer.Exit(code=1)
    if audio_file is not None and mic:
        # Recording would silently ignore the file the user named — say so instead.
        typer.echo(f"--mic records a new clip; drop it to transcribe {audio_file}", err=True)
        raise typer.Exit(code=1)

    settings = load_settings()
    sc = remote_client()
    task = "translate" if translate else "transcribe"

    try:
        if sc is not None:  # remote mode: the service transcribes, we just print the answer
            if mic:
                payload = _wav_bytes(_record(seconds, settings.sample_rate), settings.sample_rate)
                filename = "recording.wav"
            else:
                payload, filename = audio_file.read_bytes(), audio_file.name
            body = sc.transcribe(
                payload,
                filename,
                model=asr_model,
                # Forwarded verbatim: the service's own resolver distinguishes an absent
                # language (its configured default) from "auto" (always detect), and
                # collapsing "auto" to None here would silently pick up that default.
                language=language,
                task=task,
                response_format=fmt,
                prompt=prompt,
                word_timestamps=word_timestamps,
            )
        else:
            # Through the same resolver the server and `auris asr pull` use, so an unknown
            # id is one clean error everywhere instead of a HuggingFace download attempt
            # here and a 422 remotely. Checked before recording, not after.
            model = resolve_asr_model(asr_model, settings)
            if mic:
                audio, sample_rate = _record(seconds, settings.sample_rate), settings.sample_rate
            else:
                audio, sample_rate = io.load_audio(audio_file)
            options = TranscribeOptions(
                model=model,
                language=_resolve_language(language),
                task=task,
                word_timestamps=word_timestamps,
                vad_filter=settings.asr_vad,
                initial_prompt=prompt,
            )
            transcript = transcribe_detailed(audio, sample_rate, options=options, settings=settings)
            body, _content_type = render(transcript, fmt, word_timestamps, task)

        # json/verbose_json come back as a dict from both paths; everything else is text
        # already. ensure_ascii=False keeps accents and CJK readable, as the service sends them.
        text = body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False)
        if out is not None:
            out.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
            typer.echo(f"Wrote {out}", err=True)
        else:
            typer.echo(text)
    except (ClientError, OSError, ValueError, LookupError, sf.SoundFileError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
