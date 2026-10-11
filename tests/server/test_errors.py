from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auris.server.errors import install_error_handlers


class OutOfMemoryError(RuntimeError):  # mimics torch.cuda.OutOfMemoryError (a RuntimeError)
    pass


def _app_that_raises(exc: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_oom_maps_to_503_gpu_oom():
    c = _app_that_raises(OutOfMemoryError("CUDA out of memory"))
    r = c.get("/boom")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "gpu_oom"


def test_plain_runtimeerror_still_500():
    c = _app_that_raises(RuntimeError("some other failure"))
    r = c.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal"


def test_runtimeerror_500_logs_the_traceback():
    c = _app_that_raises(RuntimeError("some other failure"))
    with structlog.testing.capture_logs() as logs:
        r = c.get("/boom")
    assert r.status_code == 500
    errors = [e for e in logs if e["log_level"] == "error"]
    assert errors, "a 500 must log the exception server-side"
    assert errors[0]["exc_info"]


def test_pipeline_disabled_maps_to_503():
    from auris.server.errors import PipelineDisabled

    c = _app_that_raises(
        PipelineDisabled("speech-to-text is disabled on this server (set AURIS_STT_ENABLED=true)")
    )
    r = c.get("/boom")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "pipeline_disabled"
    assert "AURIS_STT_ENABLED" in r.json()["error"]["message"]


def test_forbidden_maps_to_403():
    from auris.server.errors import Forbidden

    r = _app_that_raises(Forbidden("admin only")).get("/boom")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_name_taken_maps_to_409():
    from auris.voice_library.exceptions import NameTaken

    r = _app_that_raises(NameTaken("taken")).get("/boom")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_unexpected_exception_500_logs_the_traceback():
    class Unexpected(Exception):
        pass

    c = _app_that_raises(Unexpected("totally unexpected"))
    with structlog.testing.capture_logs() as logs:
        r = c.get("/boom")
    assert r.status_code == 500
    errors = [e for e in logs if e["log_level"] == "error"]
    assert errors, "a 500 must log the exception server-side"
    assert errors[0]["exc_info"]
