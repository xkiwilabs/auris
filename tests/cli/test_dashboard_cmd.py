from __future__ import annotations

from typer.testing import CliRunner

import auris.client.config as cfg
from auris.cli.commands import dashboard as dashboard_cmd
from auris.cli.main import app

runner = CliRunner()


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "USER_CONFIG", tmp_path / "u.env")
    monkeypatch.delenv("AURIS_BASE_URL", raising=False)
    monkeypatch.delenv("AURIS_AUTH_TOKEN", raising=False)


def test_dashboard_asks_for_nothing_and_leaves_sign_in_to_the_browser(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    cfg.save_client_config(base_url="http://h:8011")
    calls = []
    monkeypatch.setattr(dashboard_cmd, "serve_dashboard", lambda *a, **k: calls.append((a, k)))
    result = runner.invoke(app, ["dashboard", "--no-open"], input="")
    assert result.exit_code == 0, result.output
    assert "Email" not in result.output and "Password" not in result.output
    assert calls == [(("http://h:8011",), {"port": 8765, "open_browser": False})]


def test_dashboard_without_a_service_says_how_to_set_one(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 1
    assert "auris login" in result.output
