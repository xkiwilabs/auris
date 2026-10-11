from __future__ import annotations

from typer.testing import CliRunner

import auris.client.config as cfg
from auris.cli.main import app

runner = CliRunner()


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "USER_CONFIG", tmp_path / "u.env")
    monkeypatch.delenv("AURIS_BASE_URL", raising=False)
    monkeypatch.delenv("AURIS_AUTH_TOKEN", raising=False)


def test_config_set_url_then_token_via_stdin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert runner.invoke(app, ["config", "set", "--url", "http://h:8011"]).exit_code == 0
    # token is read from stdin (never passed on the command line)
    assert runner.invoke(app, ["config", "token"], input="supersecrettoken\n").exit_code == 0
    out = runner.invoke(app, ["config", "show"]).output
    assert "http://h:8011" in out
    assert "supersecrettoken" not in out and "oken" in out  # masked, not raw


def test_config_token_updates_only_token(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    runner.invoke(app, ["config", "set", "--url", "http://h:8011"])
    runner.invoke(app, ["config", "token"], input="old\n")
    assert runner.invoke(app, ["config", "token"], input="newtoken123\n").exit_code == 0
    c = cfg.load_client_config()
    assert c["base_url"] == "http://h:8011" and c["token"] == "newtoken123"


def test_config_clear(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    runner.invoke(app, ["config", "set", "--url", "http://h:8011"])
    runner.invoke(app, ["config", "token"], input="t\n")
    assert runner.invoke(app, ["config", "clear"]).exit_code == 0
    assert cfg.load_client_config()["base_url"] == ""


def test_login_without_a_token_asks_only_for_the_url(tmp_path, monkeypatch):
    # What setup-client runs: the dashboard needs nothing more than the address.
    _isolate(tmp_path, monkeypatch)
    result = runner.invoke(app, ["login", "--no-token"], input="http://h:8011\n")
    assert result.exit_code == 0, result.output
    assert "token" not in result.output.lower().split("auris config token")[0]
    assert cfg.load_client_config() == {"base_url": "http://h:8011", "token": ""}
    assert "auris dashboard" in result.output and "auris config token" in result.output


def test_login_says_what_the_api_key_is_for(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import auris.cli.commands.config as config_cmd

    prompts = []

    def fake_prompt(text, **kw):
        prompts.append(text)
        return "http://h:8011" if text == "Service URL" else ""

    monkeypatch.setattr(config_cmd, "_interactive", lambda: True)
    monkeypatch.setattr(config_cmd.typer, "prompt", fake_prompt)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0, result.output
    key_prompt = prompts[1]
    assert "API key" in key_prompt and "command-line" in key_prompt
    assert "dashboard" in key_prompt  # ...and that the dashboard doesn't need one
