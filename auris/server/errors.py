from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from soundfile import SoundFileError
from starlette.exceptions import HTTPException as StarletteHTTPException

from auris.log import get_logger
from auris.server.pool import PoolFull
from auris.voice_library.exceptions import (
    AmbiguousVoice,
    ClipNotFound,
    KeyNotFound,
    LastActiveAdmin,
    NameTaken,
    UserNotFound,
)

_log = get_logger("auris.server.errors")


class BadAudio(Exception):
    """Uploaded audio could not be decoded, or violates a size/format limit (→ 400 bad_audio)."""


class Forbidden(Exception):
    """Authenticated, but not allowed — a non-admin principal on an admin route (→ 403)."""


class PasswordChangeRequired(Exception):
    """Authenticated, but the account must set a new password first (→ 403).

    A distinct code from `forbidden` so the dashboard can tell "you may not do this" from
    "you must change your password", without a second endpoint to probe."""


class CurrentPasswordInvalid(Exception):
    """The session is valid; the `current_password` in the body is not (→ 403).

    Deliberately NOT 401. The caller's *session* authenticated fine — what failed is the
    re-authentication check on the request body. `static/js/api.js` treats any 401 as "the session
    is gone" and sends the user back to sign-in, so a 401 here logged a user out for mistyping
    their own current password, mid-password-change. 403 with its own code keeps that global
    handler honest: 401 means no valid session, nothing else."""


class PipelineDisabled(Exception):
    """A route exists but its pipeline is switched off by config (→ 503 pipeline_disabled).

    503 rather than 404 on purpose: the capability is configured off, not absent."""


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PoolFull)
    async def _pool_full(_r: Request, _e: PoolFull):
        return _envelope(429, "overloaded", "all workers busy; retry shortly")

    @app.exception_handler(LookupError)
    async def _not_found(_r: Request, e: LookupError):
        return _envelope(404, "voice_not_found", str(e))

    # Starlette walks the exception's MRO and takes the most derived registered handler, so this
    # claims KeyNotFound while a bare LookupError still reads as voice_not_found above.
    @app.exception_handler(ClipNotFound)
    async def _clip_not_found(_r: Request, e: ClipNotFound):
        return _envelope(404, "clip_not_found", str(e))

    @app.exception_handler(KeyNotFound)
    async def _key_not_found(_r: Request, e: KeyNotFound):
        return _envelope(404, "key_not_found", str(e))

    @app.exception_handler(UserNotFound)
    async def _user_not_found(_r: Request, e: UserNotFound):
        return _envelope(404, "user_not_found", str(e))

    @app.exception_handler(ValueError)
    async def _bad_request(_r: Request, e: ValueError):
        return _envelope(422, "invalid_request", str(e))

    @app.exception_handler(asyncio.TimeoutError)
    async def _timeout(_r: Request, _e: asyncio.TimeoutError):
        return _envelope(504, "timeout", "request timed out")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, e: StarletteHTTPException):
        code = "unauthorized" if e.status_code == 401 else "error"
        return _envelope(e.status_code, code, str(e.detail))

    @app.exception_handler(BadAudio)
    async def _bad_audio(_r: Request, e: BadAudio):
        return _envelope(400, "bad_audio", str(e))

    @app.exception_handler(PipelineDisabled)
    async def _pipeline_disabled(_r: Request, e: PipelineDisabled):
        return _envelope(503, "pipeline_disabled", str(e))

    @app.exception_handler(PasswordChangeRequired)
    async def _password_change_required(_r: Request, e: PasswordChangeRequired):
        return _envelope(403, "password_change_required", str(e))

    @app.exception_handler(CurrentPasswordInvalid)
    async def _current_password_invalid(_r: Request, e: CurrentPasswordInvalid):
        return _envelope(403, "current_password_invalid", str(e))

    @app.exception_handler(Forbidden)
    async def _forbidden(_r: Request, e: Forbidden):
        return _envelope(403, "forbidden", str(e))

    @app.exception_handler(NameTaken)
    async def _conflict(_r: Request, e: NameTaken):
        return _envelope(409, "conflict", str(e))

    @app.exception_handler(AmbiguousVoice)
    async def _ambiguous_voice(_r: Request, e: AmbiguousVoice):
        return _envelope(409, "conflict", str(e))

    @app.exception_handler(LastActiveAdmin)
    async def _last_admin(_r: Request, e: LastActiveAdmin):
        return _envelope(409, "conflict", str(e))

    @app.exception_handler(SoundFileError)
    async def _bad_audio_sf(_r: Request, e: SoundFileError):
        # libsndfile messages can include the temp-file path; keep them off the wire.
        return _envelope(400, "bad_audio", "could not decode uploaded audio")

    @app.exception_handler(RuntimeError)
    async def _runtime(r: Request, e: RuntimeError):
        # torch.cuda.OutOfMemoryError is a RuntimeError subclass; match by name so we
        # never import torch here. Everything else stays a generic 500.
        if type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower():
            _log.warning("GPU out of memory", path=r.url.path)
            return _envelope(503, "gpu_oom", "GPU out of memory")
        _log.error("unhandled error", path=r.url.path, exc_info=e)
        return _envelope(500, "internal", "internal server error")

    @app.exception_handler(Exception)
    async def _unexpected(r: Request, e: Exception):
        # The only place the traceback is recorded; the client just gets the envelope.
        _log.error("unhandled error", path=r.url.path, exc_info=e)
        return _envelope(500, "internal", "internal server error")
