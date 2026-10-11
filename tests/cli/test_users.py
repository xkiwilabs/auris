from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import db, users

runner = CliRunner()


def _read_users():
    async def go():
        conn = await db.connect(load_settings().db_path)
        try:
            await db.create_tables(conn)
            return await users.list_users(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_add_creates_an_account_and_prints_the_password_once(cli_data_dir):
    result = runner.invoke(app, ["users", "add", "alice@example.com"])
    assert result.exit_code == 0, result.output
    rows = _read_users()
    assert [u.email for u in rows] == ["alice@example.com"]
    assert rows[0].must_change_password is True
    assert rows[0].role == "user"
    # The printed password must actually be the account's. Located by its label rather than by
    # position, so the command stays free to print guidance after it.
    lines = [ln.strip() for ln in result.output.strip().splitlines()]
    label = next(i for i, ln in enumerate(lines) if ln.startswith("Initial password for"))
    printed = lines[label + 1]
    assert len(printed) >= 12

    async def check():
        conn = await db.connect(load_settings().db_path)
        try:
            return await users.verify_login(conn, "alice@example.com", printed)
        finally:
            await conn.close()

    assert asyncio.run(check()) is not None


def test_add_with_the_admin_role(cli_data_dir):
    assert (
        runner.invoke(app, ["users", "add", "boss@example.com", "--role", "admin"]).exit_code == 0
    )
    assert _read_users()[0].role == "admin"


def test_a_password_is_never_accepted_as_an_argument(cli_data_dir):
    """Credentials must not reach shell history or a process listing."""
    result = runner.invoke(
        app, ["users", "add", "alice@example.com", "--password", "hunter2hunter2"]
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


def test_add_refuses_a_duplicate_and_a_malformed_email(cli_data_dir):
    runner.invoke(app, ["users", "add", "alice@example.com"])
    dup = runner.invoke(app, ["users", "add", "Alice@Example.com"])
    assert dup.exit_code == 1
    assert "already exists" in dup.output
    bad = runner.invoke(app, ["users", "add", "nobody"])
    assert bad.exit_code == 1
    assert "not a valid email" in bad.output


def test_list_shows_accounts_and_their_state(cli_data_dir):
    runner.invoke(app, ["users", "add", "alice@example.com"])
    runner.invoke(app, ["users", "add", "boss@example.com", "--role", "admin"])
    out = runner.invoke(app, ["users", "list"]).output
    assert "alice@example.com" in out and "boss@example.com" in out
    assert "admin" in out
    assert "must change password" in out


def test_list_is_helpful_when_there_are_no_accounts(cli_data_dir):
    out = runner.invoke(app, ["users", "list"]).output
    assert "auris users add" in out


def test_reset_prints_a_new_password_and_forces_a_change(cli_data_dir):
    runner.invoke(app, ["users", "add", "alice@example.com"])

    async def settle():
        conn = await db.connect(load_settings().db_path)
        try:
            u = await users.get_user_by_email(conn, "alice@example.com")
            await users.set_password(conn, u.id, "chosen-by-the-user")
        finally:
            await conn.close()

    asyncio.run(settle())

    result = runner.invoke(app, ["users", "reset", "alice@example.com"])
    assert result.exit_code == 0
    assert _read_users()[0].must_change_password is True


def test_disable_then_enable(cli_data_dir):
    runner.invoke(app, ["users", "add", "alice@example.com"])
    assert runner.invoke(app, ["users", "disable", "alice@example.com"]).exit_code == 0
    assert _read_users()[0].disabled_at is not None
    assert runner.invoke(app, ["users", "enable", "alice@example.com"]).exit_code == 0
    assert _read_users()[0].disabled_at is None


def test_disabling_the_last_admin_is_refused(cli_data_dir):
    runner.invoke(app, ["users", "add", "boss@example.com", "--role", "admin"])
    result = runner.invoke(app, ["users", "disable", "boss@example.com"])
    assert result.exit_code == 1
    assert "last active admin" in result.output


def test_an_unknown_email_exits_nonzero(cli_data_dir):
    result = runner.invoke(app, ["users", "reset", "nobody@example.com"])
    assert result.exit_code == 1
    assert "no account" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["users", "add", "alice@example.com"],
        ["users", "list"],
        ["users", "reset", "alice@example.com"],
        ["users", "disable", "alice@example.com"],
        ["users", "enable", "alice@example.com"],
    ],
    ids=["add", "list", "reset", "disable", "enable"],
)
def test_the_verbs_refuse_to_run_in_remote_mode(cli_data_dir, monkeypatch, argv):
    """They write the *server's* own users table, so on a client box they would silently write a
    database the server never reads.

    Every verb, not just one: the guard is a per-command call, so covering a single verb would let
    a refactor drop it from the other four with the suite still green.
    """
    monkeypatch.setenv("AURIS_BASE_URL", "http://example.invalid:8011")
    result = runner.invoke(app, argv)
    assert result.exit_code == 1, result.output
    assert "must run on the server box" in result.output
    assert "/v1/users" in result.output
