from __future__ import annotations

from auris.config import Settings


def test_defaults(monkeypatch):
    for var in ("AURIS_DEFAULT_MODEL", "AURIS_SAMPLE_RATE", "AURIS_DEVICE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.default_model == "f5_tts"
    assert s.sample_rate == 24000
    assert s.device == "auto"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("AURIS_DEVICE", "cpu")
    s = Settings()
    assert s.data_dir == tmp_path / "d"
    assert s.device == "cpu"


def test_derived_paths(tmp_path):
    s = Settings(data_dir=tmp_path)
    assert s.voices_dir == tmp_path / "voices"
    assert s.db_path == tmp_path / "auris.db"


def test_server_settings_defaults_and_env(monkeypatch):
    # Hermetic: ignore any deployment .env (and stray AURIS_* env) so the defaults are exact
    # even on a box that has a real .env in the repo root.
    for var in (
        "AURIS_BIND_HOST",
        "AURIS_PORT",
        "AURIS_AUTH_TOKEN",
        "AURIS_GPU_DEVICES",
        "AURIS_GPU_WORKERS",
        "AURIS_QUEUE_MAXSIZE",
        "AURIS_MAX_INPUT_CHARS",
        "AURIS_DEFAULT_FORMAT",
    ):
        monkeypatch.delenv(var, raising=False)

    s = Settings(_env_file=None)
    assert s.bind_host == "127.0.0.1"
    assert s.port == 8001
    assert s.auth_token == ""  # empty => server refuses to start (checked in app)
    assert s.gpu_devices == "auto"
    assert s.gpu_workers == 2
    assert s.queue_maxsize == 8
    assert s.max_input_chars == 2000
    assert s.default_format == "mp3"
    assert s.kokoro_enabled is True
    assert s.kokoro_device == "auto"
    assert s.kokoro_workers == 1

    # env vars override (they take precedence over .env too)
    monkeypatch.setenv("AURIS_GPU_WORKERS", "4")
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    s2 = Settings(_env_file=None)
    assert s2.gpu_workers == 4
    assert s2.auth_token == "secret"
