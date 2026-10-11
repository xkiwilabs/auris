from __future__ import annotations

import pytest

from auris.audio.asr_models import CATALOGUE, resolve_asr_model
from auris.config import load_settings


def test_catalogue_default_is_a_catalogue_entry():
    settings = load_settings()
    assert settings.asr_model in {m.id for m in CATALOGUE}


def test_missing_model_resolves_to_the_configured_default(monkeypatch):
    monkeypatch.setenv("AURIS_ASR_MODEL", "small.en")
    settings = load_settings()
    assert resolve_asr_model(None, settings) == "small.en"
    assert resolve_asr_model("", settings) == "small.en"


def test_openai_alias_resolves_to_the_configured_default(monkeypatch):
    # Real OpenAI clients always send "whisper-1", which is not a faster-whisper id.
    monkeypatch.setenv("AURIS_ASR_MODEL", "small.en")
    assert resolve_asr_model("whisper-1", load_settings()) == "small.en"


def test_catalogue_model_is_allowed_by_default():
    assert resolve_asr_model("medium.en", load_settings()) == "medium.en"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="unknown STT model"):
        resolve_asr_model("gpt-4o-transcribe", load_settings())


def test_allowlist_narrows_the_catalogue(monkeypatch):
    monkeypatch.setenv("AURIS_ASR_MODELS", "base.en, small.en")
    settings = load_settings()
    assert settings.asr_allowed == ("base.en", "small.en")
    assert resolve_asr_model("small.en", settings) == "small.en"
    with pytest.raises(ValueError, match="medium.en"):
        resolve_asr_model("medium.en", settings)


def test_configured_default_is_always_allowed(monkeypatch):
    monkeypatch.setenv("AURIS_ASR_MODEL", "large-v3-turbo")
    monkeypatch.setenv("AURIS_ASR_MODELS", "tiny.en")
    settings = load_settings()
    assert settings.asr_allowed == ("large-v3-turbo", "tiny.en")
    assert resolve_asr_model("large-v3-turbo", settings) == "large-v3-turbo"


def test_pipeline_flags_default_to_enabled():
    s = load_settings()
    assert s.stt_enabled is True
    assert s.tts_enabled is True
    assert s.asr_workers == 1
    assert s.asr_language == ""  # "" => auto-detect
    assert s.asr_vad is True
    assert s.asr_max_upload_mb == 250
    assert s.asr_timeout_s == 600.0


def test_pipeline_flags_read_the_environment(monkeypatch):
    monkeypatch.setenv("AURIS_TTS_ENABLED", "false")
    monkeypatch.setenv("AURIS_ASR_WORKERS", "3")
    monkeypatch.setenv("AURIS_ASR_LANGUAGE", "fr")
    s = load_settings()
    assert s.tts_enabled is False
    assert s.asr_workers == 3
    assert s.asr_language == "fr"
