from __future__ import annotations

import types

from typer.testing import CliRunner

from auris.cli.commands import asr as asr_cmd
from auris.cli.main import app

runner = CliRunner()


def test_asr_list_shows_catalogue_and_active_model(monkeypatch):
    monkeypatch.setenv("AURIS_ASR_MODEL", "small.en")
    monkeypatch.setenv("AURIS_ASR_DEVICE", "cpu")
    monkeypatch.setattr(asr_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(asr_cmd, "_cached", lambda model_id: False)

    result = runner.invoke(app, ["asr", "list"])
    assert result.exit_code == 0, result.output
    assert "active: small.en on cpu (int8)" in result.output
    assert "large-v3-turbo" in result.output
    assert "* small.en" in result.output


def test_asr_list_asks_the_service_in_remote_mode(monkeypatch):
    fake = types.SimpleNamespace(
        asr_models=lambda: {
            "default": "medium.en",
            "models": [
                {"id": "medium.en", "params": "769M", "lang": "en", "desc": "high accuracy"}
            ],
        }
    )
    monkeypatch.setattr(asr_cmd, "remote_client", lambda: fake)
    result = runner.invoke(app, ["asr", "list"])
    assert result.exit_code == 0, result.output
    assert "service default: medium.en" in result.output
    assert "* medium.en" in result.output


def test_asr_pull_rejects_an_unknown_model(monkeypatch):
    result = runner.invoke(app, ["asr", "pull", "whisper-1"])
    assert result.exit_code == 1
    assert "unknown model" in result.output


def test_asr_pull_downloads_the_requested_models(monkeypatch):
    pulled = []
    fake = types.ModuleType("faster_whisper")
    fake.download_model = lambda model_id: (pulled.append(model_id), f"/cache/{model_id}")[1]
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", fake)

    result = runner.invoke(app, ["asr", "pull", "tiny.en", "small.en"])
    assert result.exit_code == 0, result.output
    assert pulled == ["tiny.en", "small.en"]
    assert "/cache/small.en" in result.output


def test_asr_pull_defaults_to_the_configured_model(monkeypatch):
    monkeypatch.setenv("AURIS_ASR_MODEL", "distil-small.en")
    pulled = []
    fake = types.ModuleType("faster_whisper")
    fake.download_model = lambda model_id: (pulled.append(model_id), "/cache")[1]
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", fake)

    assert runner.invoke(app, ["asr", "pull"]).exit_code == 0
    assert pulled == ["distil-small.en"]
