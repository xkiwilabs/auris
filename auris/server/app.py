from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from auris.config import load_settings
from auris.runtime import ensure_valid_hashseed
from auris.server.auth import auth_dependency
from auris.server.errors import install_error_handlers
from auris.server.routers import auth as auth_router
from auris.server.routers import dashboard, health


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan: load settings once, open the shared WAL DB, build + warm the
    GPU pools (off the event loop), then serve. /ready flips true only after warm-up.

    Each pipeline is built only if it is enabled, so an STT-only or TTS-only server never
    loads the other side's weights."""
    from auris.server.asr_pool import build_asr_pool
    from auris.server.pool import build_pools
    from auris.voice_library import db

    ensure_valid_hashseed()
    settings = load_settings()
    if not settings.auth_token:
        raise RuntimeError("AURIS_AUTH_TOKEN must be set before starting auris-server")
    if not settings.stt_enabled and not settings.tts_enabled:
        raise RuntimeError(
            "both pipelines are disabled — set AURIS_STT_ENABLED or AURIS_TTS_ENABLED"
        )

    conn = await db.connect(settings.db_path)
    await db.create_tables(conn)
    pools = await asyncio.to_thread(build_pools, settings) if settings.tts_enabled else {}
    asr_pool = await asyncio.to_thread(build_asr_pool, settings) if settings.stt_enabled else None

    app.state.settings = settings
    app.state.db = conn
    app.state.pools = pools
    app.state.asr_pool = asr_pool
    app.state.ready = True
    try:
        yield
    finally:
        await conn.close()


def create_app(lifespan=lifespan) -> FastAPI:
    app = FastAPI(title="auris-server", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(dashboard.router)  # open: the browser landing page
    # Open assets (css/js). Mounted at /static — never at "/", which would shadow the routers.
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(health.router)  # open (no auth)
    # Open at the router level: /auth/login must be reachable without credentials, while
    # /auth/logout and /auth/me carry auth_dependency on their own route decorators.
    app.include_router(auth_router.router)
    _mount_authed(app)
    return app


def _mount_authed(app: FastAPI) -> None:
    from auris.server.routers import info, keys, openai, synthesis, voices
    from auris.server.routers import users as users_router  # aliased: `users` also names the
    # voice_library module, and app.py should not make that ambiguous for a later reader

    for r in (
        synthesis.router,
        openai.router,
        voices.router,
        keys.router,
        info.router,
        users_router.router,
    ):
        app.include_router(r, dependencies=[Depends(auth_dependency)])
