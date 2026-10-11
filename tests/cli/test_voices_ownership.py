from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import db, speakers, users

runner = CliRunner()


def _with_db(fn):
    """Run one coroutine-returning callable against the CLI's own data dir."""

    async def go():
        conn = await db.connect(load_settings().db_path)
        try:
            await db.create_tables(conn)
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


async def _seed_legacy(conn):
    await users.create_user(conn, "mike@example.com", "a-good-long-password")
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, default_model, default_speed,"
        " owner_id, visibility) VALUES ('l', 'legacy-voice', 'then', 'f5_tts', 1.0, NULL,"
        " 'shared')"
    )
    await conn.commit()


def test_adopt_claims_unowned_voices(cli_data_dir):
    _with_db(_seed_legacy)
    result = runner.invoke(app, ["voices", "adopt", "mike@example.com"])
    assert result.exit_code == 0, result.output
    assert "adopted 1 voice(s)" in result.output  # the whole phrase: "1" alone matches "11"
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert [r[0] for r in rows] == ["legacy-voice"]
    assert rows[0][4] is not None  # owner_id now set


def test_adopt_refuses_an_unknown_account(cli_data_dir):
    result = runner.invoke(app, ["voices", "adopt", "nobody@example.com"])
    assert result.exit_code == 1
    assert "no account" in result.output


async def _seed_owned_voice(conn, name, email="mike@example.com"):
    u = await users.create_user(conn, email, "a-good-long-password")
    return await speakers.create_speaker(conn, name, owner_id=u.id)


def test_share_and_unshare_round_trip(cli_data_dir):
    """The round trip only means anything on an *owned* voice — visibility is inert on an
    unowned row (see test_unshare_refuses_on_an_unowned_voice)."""
    _with_db(lambda c: _seed_owned_voice(c, "v"))
    assert runner.invoke(app, ["voices", "share", "v"]).exit_code == 0
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert rows[0][5] == "shared"
    assert runner.invoke(app, ["voices", "unshare", "v"]).exit_code == 0
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert rows[0][5] == "private"


def test_unshare_refuses_on_an_unowned_voice(cli_data_dir):
    """An unowned voice is visible to every account no matter what its visibility column says
    (speakers._VISIBLE's `s.owner_id IS NULL` clause) — unsharing it would change a column and
    nothing an operator could observe, so it must refuse instead of lying."""
    _with_db(lambda c: speakers.create_speaker(c, "legacy"))
    result = runner.invoke(app, ["voices", "unshare", "legacy"])
    assert result.exit_code == 1
    assert "no owner" in result.output
    assert "adopt" in result.output
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    # 'shared' is what an unowned row is created as (speakers.UNOWNED_IS_ALWAYS_SHARED); the
    # point here is that the refused `unshare` left it exactly as it was.
    assert rows[0][5] == "shared"  # unchanged


def test_share_on_an_unowned_voice_succeeds_with_a_note(cli_data_dir):
    _with_db(lambda c: speakers.create_speaker(c, "legacy"))
    result = runner.invoke(app, ["voices", "share", "legacy"])
    assert result.exit_code == 0, result.output
    assert "already visible to every account" in result.output
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert rows[0][5] == "shared"


def test_adopt_refuses_to_run_in_remote_mode(cli_data_dir, monkeypatch):
    """It writes the *server's* table; on a client box it would silently write a database the
    server never reads."""
    monkeypatch.setenv("AURIS_BASE_URL", "http://example.invalid:8011")
    result = runner.invoke(app, ["voices", "adopt", "mike@example.com"])
    assert result.exit_code == 1
    assert "must run on the server box" in result.output


async def _seed_owned(conn):
    u = await users.create_user(conn, "mike@example.com", "a-good-long-password")
    await speakers.create_speaker(conn, "mine", owner_id=u.id, visibility=speakers.SHARED)


def test_listing_shows_owner_and_visibility(cli_data_dir):
    _with_db(_seed_owned)
    out = runner.invoke(app, ["voices"]).output
    assert "mine" in out
    assert "shared" in out
    assert "mike@example.com" in out


def test_bare_voices_still_lists(cli_data_dir):
    """The group conversion must not break `auris voices` with no subcommand — every doc
    example and every user's habit depends on it."""
    result = runner.invoke(app, ["voices"])
    assert result.exit_code == 0, result.output
    assert "Presets" in result.output


# --- fix round 1: share/unshare resolution (4-branch precedence) ---------------------------


async def _seed_two_owners_same_name(conn, name="narrator"):
    alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
    bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
    a = await speakers.create_speaker(conn, name, owner_id=alice.id)
    b = await speakers.create_speaker(conn, name, owner_id=bob.id)
    return alice, bob, a, b


def test_bare_unshare_refuses_an_ambiguous_name_and_names_both_owners(cli_data_dir):
    _with_db(_seed_two_owners_same_name)
    result = runner.invoke(app, ["voices", "unshare", "narrator"])
    assert result.exit_code == 1
    assert "alice@example.com" in result.output
    assert "bob@example.com" in result.output
    assert "--owner" in result.output


def test_owner_disambiguates_and_touches_only_that_account_row(cli_data_dir):
    alice, bob, a, b = _with_db(_seed_two_owners_same_name)
    result = runner.invoke(app, ["voices", "share", "narrator", "--owner", "bob@example.com"])
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[bob.id] == "shared"
    assert rows[alice.id] == "private"  # untouched — not just a status code, the actual row


async def _seed_unowned_and_owned_same_name(conn, name="narrator"):
    carol = await users.create_user(conn, "carol@example.com", "a-good-long-password")
    owned = await speakers.create_speaker(conn, name, owner_id=carol.id)
    unowned = await speakers.create_speaker(conn, name, owner_id=None)
    return carol, owned, unowned


def test_bare_share_prefers_the_unowned_voice_over_an_owned_namesake(cli_data_dir):
    carol, owned, unowned = _with_db(_seed_unowned_and_owned_same_name)
    result = runner.invoke(app, ["voices", "share", "narrator"])
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[None] == "shared"
    assert rows[carol.id] == "private"  # the owned namesake was not touched


def test_share_owner_option_with_unknown_account_hints_users_add(cli_data_dir):
    _with_db(lambda c: _seed_owned_voice(c, "v"))
    result = runner.invoke(app, ["voices", "share", "v", "--owner", "nobody@example.com"])
    assert result.exit_code == 1
    assert "nobody@example.com" in result.output
    assert "auris users add" in result.output


# --- fix round 1: --owner on bare `auris voices` -----------------------------------------


async def _seed_two_owners_each_one_voice(conn):
    alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
    bob = await users.create_user(conn, "bob@example.com", "a-good-long-password")
    await speakers.create_speaker(conn, "alice-voice", owner_id=alice.id)
    await speakers.create_speaker(conn, "bob-voice", owner_id=bob.id)


def test_owner_filter_on_listing_shows_only_that_accounts_voices(cli_data_dir):
    _with_db(_seed_two_owners_each_one_voice)
    out = runner.invoke(app, ["voices", "--owner", "alice@example.com"]).output
    assert "alice-voice" in out
    assert "bob-voice" not in out


def test_owner_filter_still_lists_presets(cli_data_dir):
    _with_db(_seed_two_owners_each_one_voice)
    out = runner.invoke(app, ["voices", "--owner", "alice@example.com"]).output
    assert "Presets" in out
    assert "narrator-us-f" in out


def test_owner_filter_unknown_account_hints_users_add(cli_data_dir):
    result = runner.invoke(app, ["voices", "--owner", "nobody@example.com"])
    assert result.exit_code == 1
    assert "nobody@example.com" in result.output
    assert "auris users add" in result.output


# --- fix round 4: `--owner` works in BOTH positions, or not at all ------------------------
#
# `--owner` is declared on the group callback (for the listing filter) and on each subcommand,
# so Typer accepts it on either side of the verb. The callback only read its copy when no
# subcommand ran, so the pre-subcommand position — the one `auris voices --help` advertises —
# was accepted and dropped: with only Alice owning `solo`,
# `auris voices --owner bob@example.com share solo` made ALICE's private voice readable by every
# account and printed `solo: shared`, exit 0. These tests assert the row, not the exit code: a
# status code cannot tell a correct resolution from the wrong account's.

_BOTH_POSITIONS = pytest.mark.parametrize(
    "argv",
    [
        ["voices", "--owner", "bob@example.com", "share", "narrator"],
        ["voices", "share", "narrator", "--owner", "bob@example.com"],
    ],
    ids=["owner-before-the-subcommand", "owner-after-the-subcommand"],
)


@_BOTH_POSITIONS
def test_both_owner_positions_share_the_same_row(cli_data_dir, argv):
    alice, bob, _a, _b = _with_db(_seed_two_owners_same_name)
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[bob.id] == "shared"
    assert rows[alice.id] == "private"  # the other account's row, untouched


@pytest.mark.parametrize(
    "argv",
    [
        ["voices", "--owner", "bob@example.com", "share", "solo"],
        ["voices", "share", "solo", "--owner", "bob@example.com"],
    ],
    ids=["owner-before-the-subcommand", "owner-after-the-subcommand"],
)
def test_both_owner_positions_refuse_a_name_that_account_does_not_own(cli_data_dir, argv):
    """The reported bug, verbatim: only Alice owns `solo`, and the operator names Bob. Both
    positions must refuse, and Alice's private voice must still be private — the original
    failure made it public and said `solo: shared`."""

    async def seed(conn):
        alice = await users.create_user(conn, "alice@example.com", "a-good-long-password")
        await users.create_user(conn, "bob@example.com", "a-good-long-password")
        return await speakers.create_speaker(conn, "solo", owner_id=alice.id)

    _with_db(seed)
    result = runner.invoke(app, argv)
    assert result.exit_code == 1, result.output
    assert "not found" in result.output and "bob@example.com" in result.output
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert rows[0][5] == "private"  # Alice's voice was not shared


def test_owner_in_both_positions_with_different_values_is_refused(cli_data_dir):
    """A contradiction, not a precedence question: naming two accounts for one write has no
    right answer, so refuse and name both rather than silently preferring one."""
    alice, bob, _a, _b = _with_db(_seed_two_owners_same_name)
    result = runner.invoke(
        app,
        [
            "voices",
            "--owner",
            "alice@example.com",
            "share",
            "narrator",
            "--owner",
            "bob@example.com",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "alice@example.com" in result.output and "bob@example.com" in result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[alice.id] == "private" and rows[bob.id] == "private"  # neither row written


def test_the_same_owner_in_both_positions_is_not_a_conflict(cli_data_dir):
    """Case-insensitively the same address: the `users` email index is COLLATE NOCASE, so this
    is one request typed twice, not two."""
    alice, bob, _a, _b = _with_db(_seed_two_owners_same_name)
    result = runner.invoke(
        app,
        ["voices", "--owner", "Bob@Example.COM", "share", "narrator", "--owner", "bob@example.com"],
    )
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[bob.id] == "shared" and rows[alice.id] == "private"


@pytest.mark.parametrize(
    "argv",
    [
        ["voices", "--owner", "bob@example.com", "unshare", "narrator"],
        ["voices", "unshare", "narrator", "--owner", "bob@example.com"],
    ],
    ids=["owner-before-the-subcommand", "owner-after-the-subcommand"],
)
def test_unshare_takes_owner_in_either_position(cli_data_dir, argv):
    """`share` was the only verb tested with `--owner`; `unshare` is the one that can hide a
    voice colleagues are using, so it is the more dangerous of the two to resolve wrongly."""
    alice, bob, _a, _b = _with_db(_seed_two_owners_same_name)
    _with_db(lambda c: speakers.set_visibility(c, _b.id, speakers.SHARED))
    _with_db(lambda c: speakers.set_visibility(c, _a.id, speakers.SHARED))
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[bob.id] == "private"
    assert rows[alice.id] == "shared"  # untouched


def test_adopt_refuses_the_group_owner_flag(cli_data_dir):
    """`adopt` takes the account as its argument, so the group's `--owner` means nothing to it
    — and an accepted-then-ignored flag is exactly what shared the wrong account's voice."""
    _with_db(_seed_legacy)
    result = runner.invoke(
        app, ["voices", "--owner", "mike@example.com", "adopt", "mike@example.com"]
    )
    assert result.exit_code == 1, result.output
    assert "--owner does not apply" in result.output
    rows = _with_db(lambda c: speakers.list_visible_with_stats(c, None))
    assert rows[0][4] is None  # still unowned: nothing happened


def test_share_refuses_a_name_that_does_not_exist(cli_data_dir):
    result = runner.invoke(app, ["voices", "share", "nope"])
    assert result.exit_code == 1
    assert "'nope' not found" in result.output


def test_bare_share_resolves_the_single_owned_namesake_when_none_is_unowned(cli_data_dir):
    """Step 3 of the precedence, named rather than covered incidentally: with no unowned row
    and exactly one voice by this name anywhere, a bare `share` is unambiguous. The operator is
    admin on the box, so reaching an account's voice is legitimate — it just must not happen
    when the name is ambiguous."""
    sp = _with_db(lambda c: _seed_owned_voice(c, "solo", "alice@example.com"))
    result = runner.invoke(app, ["voices", "share", "solo"])
    assert result.exit_code == 0, result.output
    rows = {r[4]: r[5] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert rows[sp.owner_id] == "shared"


# --- fix round 4: adopt on a name collision ------------------------------------------------


async def _seed_legacy_clashing_with_an_owned_name(conn):
    u = await users.create_user(conn, "mike@example.com", "a-good-long-password")
    await speakers.create_speaker(conn, "narrator", owner_id=u.id)
    await speakers.create_speaker(conn, "narrator", owner_id=None)
    await speakers.create_speaker(conn, "other-legacy", owner_id=None)
    return u


def test_adopt_reports_a_name_collision_instead_of_crashing(cli_data_dir):
    """An admin who created `narrator` under their new account before remembering to adopt hits
    this on the first try — it used to raise sqlite3.IntegrityError, i.e. a traceback with no
    message at all. SQLite aborts the whole UPDATE, so the message has to say that nothing was
    adopted, and the operator has to be told what to do about it."""
    u = _with_db(_seed_legacy_clashing_with_an_owned_name)
    result = runner.invoke(app, ["voices", "adopt", "mike@example.com"])
    assert result.exit_code == 1, result.output
    assert "IntegrityError" not in result.output
    assert "narrator" in result.output
    assert "nothing was adopted" in result.output
    assert "auris voices adopt mike@example.com" in result.output  # how to retry
    owners = {(r[0], r[4]) for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert ("other-legacy", None) in owners  # all-or-nothing: not even the clash-free row moved
    assert ("narrator", u.id) in owners


def test_adopt_succeeds_once_the_collision_is_resolved(cli_data_dir):
    """The flip side of all-or-nothing: the refusal is recoverable by renaming one side, and a
    re-run then claims everything. `adopt`'s docstring promises idempotence; this is what that
    promise is worth after a collision."""
    u = _with_db(_seed_legacy_clashing_with_an_owned_name)
    clash = _with_db(lambda c: speakers.get_speaker_by_name_and_owner(c, "narrator", None))
    _with_db(lambda c: speakers.rename_speaker(c, clash.id, "narrator-legacy"))
    result = runner.invoke(app, ["voices", "adopt", "mike@example.com"])
    assert result.exit_code == 0, result.output
    assert "adopted 2 voice(s)" in result.output
    owners = {r[4] for r in _with_db(lambda c: speakers.list_visible_with_stats(c, None))}
    assert owners == {u.id}
