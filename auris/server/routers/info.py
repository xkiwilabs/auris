from __future__ import annotations

from fastapi import APIRouter, Depends, Request

import auris
from auris.audio.transcribe import _resolve_runtime
from auris.server.deps import get_settings

router = APIRouter()


@router.get("/v1/info")
async def info(request: Request, settings=Depends(get_settings)):
    """What this server is running — for the dashboard's Models & Settings panel. Read-only;
    nothing here can be changed over HTTP."""
    pools = getattr(request.app.state, "pools", None) or {}
    asr_pool = getattr(request.app.state, "asr_pool", None)
    device, index, compute = _resolve_runtime(settings.asr_device, settings.asr_compute_type)
    return {
        "version": auris.__version__,
        "pipelines": {"stt": asr_pool is not None, "tts": bool(pools)},
        "tts": {
            name: {"workers": pool.size, "devices": list(pool.devices)}
            for name, pool in pools.items()
        },
        "asr": {
            "model": settings.asr_model,
            "workers": asr_pool.size if asr_pool is not None else 0,
            "device": device,
            "device_index": index,
            "compute_type": compute,
            "allowed": list(settings.asr_allowed),
        },
        "limits": {
            "max_input_chars": settings.max_input_chars,
            "asr_max_upload_mb": settings.asr_max_upload_mb,
            "max_upload_seconds": settings.max_upload_seconds,
        },
    }
