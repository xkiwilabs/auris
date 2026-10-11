from __future__ import annotations

import auris.client.config as cfg


def test_resolution_env_over_user_file(tmp_path, monkeypatch):
    user = tmp_path / "user.env"
    user.write_text("AURIS_BASE_URL=http://user\nAURIS_AUTH_TOKEN=utok\n")
    monkeypatch.setattr(cfg, "USER_CONFIG", user)
    monkeypatch.delenv("AURIS_BASE_URL", raising=False)
    monkeypatch.delenv("AURIS_AUTH_TOKEN", raising=False)

    c = cfg.load_client_config()
    assert c["base_url"] == "http://user" and c["token"] == "utok"

    monkeypatch.setenv("AURIS_BASE_URL", "http://env")
    assert cfg.load_client_config()["base_url"] == "http://env"  # env over user


def test_cwd_client_env_is_not_trusted(tmp_path, monkeypatch):
    # A client.env in the working directory must NOT be read (credential-redirection guard).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "client.env").write_text("AURIS_BASE_URL=http://evil\nAURIS_AUTH_TOKEN=evil\n")
    monkeypatch.setattr(cfg, "USER_CONFIG", tmp_path / "nonexistent.env")
    monkeypatch.delenv("AURIS_BASE_URL", raising=False)
    monkeypatch.delenv("AURIS_AUTH_TOKEN", raising=False)
    assert cfg.load_client_config()["base_url"] == ""  # CWD file ignored


def test_save_round_trip_and_mask(tmp_path, monkeypatch):
    user = tmp_path / "user.env"
    monkeypatch.setattr(cfg, "USER_CONFIG", user)
    monkeypatch.delenv("AURIS_BASE_URL", raising=False)
    monkeypatch.delenv("AURIS_AUTH_TOKEN", raising=False)

    cfg.save_client_config(base_url="http://x:8011", token="supersecrettoken")
    c = cfg.load_client_config()
    assert c["base_url"] == "http://x:8011" and c["token"] == "supersecrettoken"
    assert user.stat().st_mode & 0o777 == 0o600
    assert cfg.mask("supersecrettoken").endswith("oken")
    assert "secret" not in cfg.mask("supersecrettoken")
