from __future__ import annotations

from typer.testing import CliRunner

from auris.cli.main import app

runner = CliRunner()


def _add_user(email: str = "alice@example.com") -> str:
    r = runner.invoke(app, ["users", "add", email])
    assert r.exit_code == 0, r.output
    return email


def test_keys_add_prints_the_key_once_and_list_hides_it(cli_data_dir):
    owner = _add_user()
    r = runner.invoke(app, ["keys", "add", "alice", "--owner", owner])
    assert r.exit_code == 0, r.output
    key = [line for line in r.output.splitlines() if line.startswith("auris_")][0]
    assert key.startswith("auris_")

    r = runner.invoke(app, ["keys", "list"])
    assert r.exit_code == 0, r.output
    assert "alice" in r.output and key not in r.output


def test_keys_add_requires_an_owner(cli_data_dir):
    r = runner.invoke(app, ["keys", "add", "alice"])
    assert r.exit_code != 0
    assert "--owner" in r.output or "Missing option" in r.output


def test_keys_add_with_an_unknown_owner_hints_at_users_add(cli_data_dir):
    r = runner.invoke(app, ["keys", "add", "alice", "--owner", "ghost@example.com"])
    assert r.exit_code == 1
    assert "no account" in r.output
    assert "auris users add" in r.output


def test_keys_add_stores_the_owner(cli_data_dir):
    owner = _add_user()
    runner.invoke(app, ["keys", "add", "alice", "--owner", owner])
    out = runner.invoke(app, ["keys", "list"]).output
    assert owner in out


def test_keys_revoke_and_unknown(cli_data_dir):
    owner = _add_user()
    runner.invoke(app, ["keys", "add", "alice", "--owner", owner])
    r = runner.invoke(app, ["keys", "revoke", "alice"])
    assert r.exit_code == 0 and "revoked" in r.output
    r = runner.invoke(app, ["keys", "revoke", "nobody"])
    assert r.exit_code == 1 and "not found" in r.output


def test_keys_add_duplicate_fails(cli_data_dir):
    owner = _add_user()
    runner.invoke(app, ["keys", "add", "alice", "--owner", owner])
    r = runner.invoke(app, ["keys", "add", "alice", "--owner", owner])
    assert r.exit_code == 1 and "already exists" in r.output


def test_keys_list_with_no_keys_hints_how_to_make_one(cli_data_dir):
    r = runner.invoke(app, ["keys", "list"])
    assert r.exit_code == 0, r.output
    assert "no API keys yet" in r.output
    assert "--owner" in r.output


def test_keys_list_shows_no_owner_marker_for_a_legacy_key(cli_data_dir):
    """A key minted before this change (or by direct library access) is ownerless; `auris keys
    list` must say so plainly, since that is the remediation path for it."""
    import asyncio

    from auris.config import load_settings
    from auris.voice_library import db, keys

    async def seed_legacy_key() -> None:
        settings = load_settings()
        conn = await db.connect(settings.db_path)
        try:
            await db.create_tables(conn)
            await keys.create_key(conn, "legacy-bot")
        finally:
            await conn.close()

    asyncio.run(seed_legacy_key())
    out = runner.invoke(app, ["keys", "list"]).output
    assert "legacy-bot" in out
    assert "(no owner)" in out


def test_keys_add_rejects_an_unsafe_name(cli_data_dir):
    owner = _add_user()
    r = runner.invoke(app, ["keys", "add", "slash/name", "--owner", owner])
    assert r.exit_code == 1 and "letters, digits" in r.output


def test_keys_verbs_refuse_to_run_in_remote_mode(cli_data_dir, monkeypatch):
    # These verbs write the server's own key table. In remote mode they would otherwise write
    # this machine's data/auris.db and report success for a key the server will never honour.
    monkeypatch.setenv("AURIS_BASE_URL", "https://tts.example:8000")
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    for argv in (
        ["keys", "add", "remote-bob", "--owner", "alice@example.com"],
        ["keys", "list"],
        ["keys", "revoke", "x"],
    ):
        r = runner.invoke(app, argv)
        assert r.exit_code == 1, f"{argv} -> {r.output}"
        assert "must run on the server box" in r.output
        assert "tts.example" in r.output
    # …and nothing was written locally.
    monkeypatch.delenv("AURIS_BASE_URL")
    assert "no API keys yet" in runner.invoke(app, ["keys", "list"]).output
