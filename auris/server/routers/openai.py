from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from auris.audio import io
from auris.audio.asr_models import CATALOGUE_BY_ID, resolve_asr_model
from auris.audio.transcript import TranscribeOptions
from auris.log import get_logger
from auris.server import voice_access
from auris.server.asr_pool import TranscriptionJob
from auris.server.deps import get_asr_pool, get_db, get_pools, get_settings
from auris.server.encode import content_type, encode_audio
from auris.server.errors import BadAudio
from auris.server.pool import SynthesisJob
from auris.server.schemas import SpeechRequest
from auris.server.transcript_format import FORMATS, render
from auris.voice_library.resolve import resolve_voice

router = APIRouter()
_log = get_logger("auris.server.openai")


@router.post("/v1/audio/speech")
async def speech(
    request: Request,
    req: SpeechRequest,
    pools=Depends(get_pools),
    conn=Depends(get_db),
    settings=Depends(get_settings),
):
    if not req.input.strip():
        raise ValueError("input must not be empty")
    if len(req.input) > settings.max_input_chars:
        raise ValueError(f"input exceeds max_input_chars ({settings.max_input_chars})")
    # The voice (not the OpenAI `model` field) determines which auris model runs: a preset
    # voice routes to Kokoro, a cloned voice to f5-TTS.
    owner = await voice_access.owner_id_for(conn, req.owner)
    r = await resolve_voice(
        conn,
        settings,
        req.voice,
        viewer=voice_access.viewer_for(request.state.principal),
        owner=owner,
    )
    pool = pools.get(r.model)
    if pool is None:
        raise LookupError(f"voice {req.voice!r} needs model {r.model!r}, which is not loaded")
    job = SynthesisJob(req.input, r.conditioning, req.speed)

    if req.stream_format == "audio":

        async def gen():
            async for chunk in pool.submit_stream(job):
                data, _ = encode_audio(chunk, settings.sample_rate, "pcm")
                yield data

        return StreamingResponse(
            gen(),
            media_type=content_type("pcm"),
            headers={"X-Sample-Rate": str(settings.sample_rate)},
        )

    audio = await asyncio.wait_for(pool.submit_full(job), settings.request_timeout_s)
    data, ctype = await asyncio.to_thread(
        encode_audio, audio, settings.sample_rate, req.response_format
    )
    return Response(content=data, media_type=ctype)


def _model_info(model_id: str) -> dict:
    # An operator can allowlist an id outside the curated catalogue; describe it plainly.
    entry = CATALOGUE_BY_ID.get(model_id)
    if entry is None:
        return {"id": model_id, "params": "", "lang": "", "desc": ""}
    return {"id": entry.id, "params": entry.params, "lang": entry.lang, "desc": entry.desc}


@router.get("/v1/audio/models")
async def audio_models(settings=Depends(get_settings)):
    """Which STT models this server will accept in a `model` field, so a remote user can
    discover the options instead of guessing. `default` is what an omitted (or "whisper-1")
    model resolves to."""
    return {
        "default": settings.asr_model,
        "models": [_model_info(model_id) for model_id in settings.asr_allowed],
        "pipelines": {"stt": settings.stt_enabled, "tts": settings.tts_enabled},
    }


def _resolve_language(requested: str | None, settings) -> str | None:
    """None => the server default; "auto" (or an empty AURIS_ASR_LANGUAGE) => detect."""
    value = requested if requested is not None else settings.asr_language
    if not value or value.lower() == "auto":
        return None
    return value


def _decode_upload(raw: bytes, filename: str | None) -> tuple[np.ndarray, int]:
    """Decode uploaded bytes to float32 mono PCM. Runs on a worker thread."""
    suffix = Path(filename or "upload.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(raw)
        tmp.flush()
        try:
            return io.load_audio(tmp.name)  # libsndfile + ffmpeg fallback
        except (RuntimeError, sf.SoundFileError) as exc:
            # Log the underlying detail (ffmpeg stderr / paths) server-side only; the
            # client gets a generic 400 so internal details aren't leaked.
            _log.warning("audio decode failed for upload %r: %s", filename, exc)
            raise BadAudio("could not decode uploaded audio") from exc


@router.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    language: str | None = Form(default=None),
    task: str = Form(default="transcribe"),
    response_format: str = Form(default="json"),
    prompt: str | None = Form(default=None),
    timestamp_granularities: list[str] | None = Form(
        default=None, alias="timestamp_granularities[]"
    ),
    asr_pool=Depends(get_asr_pool),  # presence is the pipeline gate (503 when STT is off)
    settings=Depends(get_settings),
):
    """OpenAI-compatible speech-to-text. Accepts an uploaded audio file (any
    ffmpeg/libsndfile-decodable format, e.g. the browser's webm/opus).

    `model` is allowlist-gated; omitted or "whisper-1" uses the server default (see
    GET /v1/audio/models). `language` omitted uses AURIS_ASR_LANGUAGE; "auto" forces
    detection. `response_format` is json | verbose_json | text | srt | vtt."""
    if response_format not in FORMATS:
        raise ValueError(f"response_format must be one of {', '.join(FORMATS)}")
    options = TranscribeOptions(
        model=resolve_asr_model(model, settings),  # ValueError => 422
        language=_resolve_language(language, settings),
        task=task,
        word_timestamps="word" in (timestamp_granularities or []),
        vad_filter=settings.asr_vad,
        initial_prompt=prompt or None,
    ).validated()  # ValueError => 422

    max_bytes = settings.asr_max_upload_mb * 1024 * 1024
    too_big = f"audio exceeds asr_max_upload_mb ({settings.asr_max_upload_mb} MB)"
    # Reject on the parser's reported size first: read() would materialise the whole body
    # as one bytes object before we could. size is None if a parser doesn't report it, so
    # the post-read check below stays as the backstop.
    if file.size is not None and file.size > max_bytes:
        raise ValueError(too_big)

    raw = await file.read()
    if not raw:
        raise ValueError("file must not be empty")
    if len(raw) > max_bytes:
        raise ValueError(too_big)

    # Hold an admission slot across decode *and* submit: decoding is the expensive half
    # (float32 at the native rate, several times the upload's size), so a caller the pool
    # would reject anyway must be turned away with 429 before it allocates that.
    async with asr_pool.admit():
        audio, sr = await asyncio.to_thread(_decode_upload, raw, file.filename)
        if io.duration_s(audio, sr) > settings.asr_max_seconds:
            raise ValueError(f"audio exceeds asr_max_seconds ({settings.asr_max_seconds:.0f})")
        transcript = await asyncio.wait_for(
            asr_pool.submit(TranscriptionJob(audio, sr, options)), settings.asr_timeout_s
        )
    body, ctype = render(transcript, response_format, options.word_timestamps, options.task)
    if isinstance(body, dict):
        return JSONResponse(content=body)
    return Response(content=body, media_type=ctype)
