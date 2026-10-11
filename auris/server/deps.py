from __future__ import annotations

from fastapi import Request

from auris.server.errors import PipelineDisabled


def get_pools(request: Request) -> dict:
    """All resident pools keyed by model name (e.g. {'f5_tts': ..., 'kokoro': ...})."""
    pools = getattr(request.app.state, "pools", None)
    if not pools:
        raise PipelineDisabled(
            "text-to-speech is disabled on this server (set AURIS_TTS_ENABLED=true)"
        )
    return pools


def get_asr_pool(request: Request):
    """The transcription pool, or 503 if this server runs without the STT pipeline."""
    pool = getattr(request.app.state, "asr_pool", None)
    if pool is None:
        raise PipelineDisabled(
            "speech-to-text is disabled on this server (set AURIS_STT_ENABLED=true)"
        )
    return pool


def get_db(request: Request):
    return request.app.state.db


def get_settings(request: Request):
    return request.app.state.settings
