"""The server starts, becomes ready, and reports what it is running."""

from __future__ import annotations

import pytest

from tests.e2e.server import SEEDED_VOICE, Instance

pytestmark = pytest.mark.e2e


def test_health_and_ready(admin):
    assert admin.get("/health").json() == {"status": "ok"}
    assert admin.get("/ready").json() == {"ready": True}


def test_info_reports_both_pipelines_and_the_pinned_runtime(admin):
    info = admin.get("/v1/info").json()
    assert info["pipelines"] == {"stt": True, "tts": True}
    assert info["tts"]["f5_tts"]["workers"] == 1  # AURIS_GPU_WORKERS=1
    assert info["asr"]["model"] == "small.en"
    assert info["asr"]["workers"] == 1
    assert info["limits"]["max_input_chars"] > 0
    assert info["version"]


def test_the_seed_created_a_voice_and_a_key(admin, server: Instance):
    names = [v["name"] for v in admin.get("/v1/voices").json()]
    assert SEEDED_VOICE in names
    assert server.key.startswith("auris_")
