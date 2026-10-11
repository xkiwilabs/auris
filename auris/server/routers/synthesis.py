from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from auris.server import voice_access
from auris.server.deps import get_db, get_pools, get_settings
from auris.server.encode import content_type, encode_audio
from auris.server.pool import SynthesisJob
from auris.server.schemas import TTSRequest
from auris.voice_library.resolve import resolve_voice

router = APIRouter()


def _validate(text: str, max_chars: int) -> None:
    if not text.strip():
        raise ValueError("text must not be empty")
    if len(text) > max_chars:
        raise ValueError(f"text exceeds max_input_chars ({max_chars})")


def _pick_pool(pools: dict, voice: str, model: str):
    pool = pools.get(model)
    if pool is None:
        raise LookupError(f"voice {voice!r} needs model {model!r}, which is not loaded")
    return pool


@router.post("/v1/tts")
async def tts(
    request: Request,
    req: TTSRequest,
    pools=Depends(get_pools),
    conn=Depends(get_db),
    settings=Depends(get_settings),
) -> Response:
    _validate(req.text, settings.max_input_chars)
    owner = await voice_access.owner_id_for(conn, req.owner)
    r = await resolve_voice(
        conn,
        settings,
        req.voice,
        viewer=voice_access.viewer_for(request.state.principal),
        owner=owner,
    )
    pool = _pick_pool(pools, req.voice, r.model)
    job = SynthesisJob(req.text, r.conditioning, req.speed)
    audio = await asyncio.wait_for(pool.submit_full(job), settings.request_timeout_s)
    fmt = req.format or settings.default_format
    data, ctype = await asyncio.to_thread(encode_audio, audio, settings.sample_rate, fmt)
    return Response(content=data, media_type=ctype)


@router.post("/v1/tts/stream")
async def tts_stream(
    request: Request,
    req: TTSRequest,
    pools=Depends(get_pools),
    conn=Depends(get_db),
    settings=Depends(get_settings),
) -> StreamingResponse:
    _validate(req.text, settings.max_input_chars)
    owner = await voice_access.owner_id_for(conn, req.owner)
    r = await resolve_voice(
        conn,
        settings,
        req.voice,
        viewer=voice_access.viewer_for(request.state.principal),
        owner=owner,
    )
    pool = _pick_pool(pools, req.voice, r.model)
    job = SynthesisJob(req.text, r.conditioning, req.speed)

    async def gen():
        async for chunk in pool.submit_stream(job):
            data, _ = encode_audio(chunk, settings.sample_rate, "pcm")
            yield data

    headers = {"X-Sample-Rate": str(settings.sample_rate)}
    return StreamingResponse(gen(), media_type=content_type("pcm"), headers=headers)
