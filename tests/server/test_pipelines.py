from __future__ import annotations

import contextlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auris.config import load_settings
from auris.server.app import create_app, lifespan
from auris.voice_library import db


@pytest.fixture
def stt_only_client(tmp_path, monkeypatch):
    """A client whose TTS pipeline is switched off by config."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("AURIS_TTS_ENABLED", "false")
    settings = load_settings()

    @contextlib.asynccontextmanager
    async def test_lifespan(app):
        conn = await db.connect(settings.db_path)
        await db.create_tables(conn)
        app.state.settings = settings
        app.state.db = conn
        app.state.pools = {}  # TTS off
        app.state.asr_pool = object()  # STT on (a stub; no route below reaches it)
        app.state.ready = True
        yield
        await conn.close()

    app = create_app(lifespan=test_lifespan)
    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer secret"})
        yield c


def test_tts_returns_503_when_the_pipeline_is_disabled(stt_only_client):
    r = stt_only_client.post("/v1/tts", json={"voice": "narrator-us-f", "text": "hi"})
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "pipeline_disabled"
    assert "AURIS_TTS_ENABLED" in r.json()["error"]["message"]


def test_stt_returns_503_when_the_pipeline_is_disabled(client, monkeypatch):
    client.app.state.asr_pool = None
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")}
    )
    assert r.status_code == 503, r.text
    assert "AURIS_STT_ENABLED" in r.json()["error"]["message"]


def test_audio_models_reports_enabled_pipelines(client):
    body = client.get("/v1/audio/models").json()
    assert body["pipelines"] == {"stt": True, "tts": True}


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    """The minimum env the real lifespan needs, with both builders stubbed so no weights
    are loaded and no GPU is touched."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    return monkeypatch


def _stub_builders(monkeypatch, pools=None, asr_pool=None):
    """Replace both heavy builders; either may be a callable that fails the test if run."""
    import auris.server.asr_pool as asr_mod
    import auris.server.pool as pool_mod

    def _build_pools(settings):
        if callable(pools):
            return pools(settings)
        return pools

    def _build_asr_pool(settings):
        if callable(asr_pool):
            return asr_pool(settings)
        return asr_pool

    monkeypatch.setattr(pool_mod, "build_pools", _build_pools)
    monkeypatch.setattr(asr_mod, "build_asr_pool", _build_asr_pool)


@pytest.mark.asyncio
async def test_lifespan_refuses_to_start_with_both_pipelines_disabled(server_env):
    server_env.setenv("AURIS_STT_ENABLED", "false")
    server_env.setenv("AURIS_TTS_ENABLED", "false")
    _stub_builders(server_env, pools={}, asr_pool=object())

    with pytest.raises(RuntimeError, match="both pipelines are disabled"):
        async with lifespan(FastAPI()):
            pass  # unreachable: startup must fail before the body runs


@pytest.mark.asyncio
async def test_lifespan_skips_the_synthesis_pools_when_tts_is_disabled(server_env):
    server_env.setenv("AURIS_TTS_ENABLED", "false")
    sentinel = object()

    def _never(settings):
        raise AssertionError("build_pools must not run when TTS is disabled")

    _stub_builders(server_env, pools=_never, asr_pool=sentinel)

    app = FastAPI()
    async with lifespan(app):
        assert app.state.pools == {}
        assert app.state.asr_pool is sentinel
        assert app.state.ready is True


@pytest.mark.asyncio
async def test_lifespan_skips_the_asr_pool_when_stt_is_disabled(server_env):
    server_env.setenv("AURIS_STT_ENABLED", "false")
    sentinel = {"f5_tts": object()}

    def _never(settings):
        raise AssertionError("build_asr_pool must not run when STT is disabled")

    _stub_builders(server_env, pools=sentinel, asr_pool=_never)

    app = FastAPI()
    async with lifespan(app):
        assert app.state.pools is sentinel
        assert app.state.asr_pool is None
