from __future__ import annotations

import ast
from pathlib import Path

import pytest
import pytest_asyncio

import auris.server.routers
from auris.server import voice_access
from auris.server.auth import ADMIN, KIND_KEY, KIND_USER, Principal
from auris.server.errors import Forbidden
from auris.voice_library import db, speakers
from auris.voice_library.viewer import Viewer

ALICE, BOB = "alice-id", "bob-id"


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


def test_the_master_token_is_an_admin_with_no_account():
    v = voice_access.viewer_for(ADMIN)
    assert v == Viewer(None, True)


def test_a_user_principal_carries_its_account_and_role():
    p = Principal("alice@example.com", True, KIND_USER, ALICE)
    assert voice_access.viewer_for(p) == Viewer(ALICE, True)


def test_an_api_key_acts_as_its_owner_and_is_never_admin():
    p = Principal("laptop", False, KIND_KEY, ALICE)
    assert voice_access.viewer_for(p) == Viewer(ALICE, False)


def test_a_pre_accounts_key_owns_nothing_and_is_not_admin():
    """owner_id None + admin False. Reading "no user_id" as "unscoped" would give a legacy
    key the run of the library, which is the opposite of what the migration intends."""
    p = Principal("old-key", False, KIND_KEY, None)
    v = voice_access.viewer_for(p)
    assert v == Viewer(None, False)
    assert v.may_mutate(None) is False
    assert v.may_mutate(ALICE) is False


@pytest.mark.asyncio
async def test_reading_a_shared_voice_is_allowed_but_writing_is_not(conn):
    await speakers.create_speaker(conn, "theirs", owner_id=BOB, visibility=speakers.SHARED)
    v = Viewer(ALICE, False)
    assert (await voice_access.get_for_read(conn, "theirs", v)).owner_id == BOB
    with pytest.raises(Forbidden):
        await voice_access.get_for_write(conn, "theirs", v)


@pytest.mark.asyncio
async def test_a_private_voice_you_cannot_see_is_404_not_403(conn):
    """403 would confirm the name exists — the same disclosure the sign-in work removed."""
    await speakers.create_speaker(conn, "secret", owner_id=BOB)
    v = Viewer(ALICE, False)
    with pytest.raises(LookupError):
        await voice_access.get_for_read(conn, "secret", v)
    with pytest.raises(LookupError):  # NOT Forbidden
        await voice_access.get_for_write(conn, "secret", v)


@pytest.mark.asyncio
async def test_a_legacy_voice_is_readable_by_all_and_writable_only_by_an_admin(conn):
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
        " VALUES ('l', 'old', 'then', NULL, 'shared')"
    )
    await conn.commit()
    assert await voice_access.get_for_read(conn, "old", Viewer(ALICE, False))
    with pytest.raises(Forbidden):
        await voice_access.get_for_write(conn, "old", Viewer(ALICE, False))
    assert await voice_access.get_for_write(conn, "old", Viewer(None, True))


@pytest.mark.asyncio
async def test_owner_selects_within_the_visible_set_only(conn):
    await speakers.create_speaker(conn, "n", owner_id=ALICE, visibility=speakers.SHARED)
    await speakers.create_speaker(conn, "n", owner_id=BOB)  # private
    v = Viewer(ALICE, False)
    assert (await voice_access.get_for_read(conn, "n", v, owner=ALICE)).owner_id == ALICE
    with pytest.raises(LookupError):  # Bob's is private: invisible, not forbidden
        await voice_access.get_for_read(conn, "n", v, owner=BOB)


@pytest.mark.asyncio
async def test_a_viewer_may_write_their_own_voice(conn):
    """The most common real call: every other test in this file either denies write outright
    or grants it only to an admin, so the plain owner-writes-their-own path was never proven."""
    await speakers.create_speaker(conn, "mine", owner_id=ALICE)
    v = Viewer(ALICE, False)
    speaker = await voice_access.get_for_write(conn, "mine", v)
    assert speaker.owner_id == ALICE


@pytest.mark.asyncio
async def test_an_ownerless_viewer_is_refused_identically_for_a_new_and_a_taken_name(conn):
    """Both documented ways to mint an API key can produce Viewer(None, False) — owns nothing,
    not admin. It must not be able to create a voice it then cannot touch again; refuse it up
    front, with a message identical whether or not the name is already taken — the two tests
    this one replaces each asserted a substring and never actually compared them, so the claim
    in their names ("the same message") went untested."""
    await speakers.create_speaker(conn, "taken", owner_id=BOB, visibility=speakers.SHARED)
    v = Viewer(None, False)

    with pytest.raises(Forbidden) as new_name:
        await voice_access.find_for_clone(conn, "brand-new-name", v)
    with pytest.raises(Forbidden) as taken_name:
        await voice_access.find_for_clone(conn, "taken", v)

    new_message, taken_message = str(new_name.value), str(taken_name.value)
    assert new_message == taken_message  # the shared point of both tests this replaces
    assert "owner account" in new_message
    assert "re-issue" in new_message
    assert "owner_id" not in new_message


@pytest.mark.asyncio
async def test_an_ownerless_viewer_gets_the_same_message_from_get_for_write(conn):
    """`get_for_write` (PATCH/DELETE) must report the same actionable cause `find_for_clone`
    does for an ownerless caller, not the generic "belongs to someone else" — which is false
    for an unowned voice and uninformative for anyone else's."""
    await speakers.create_speaker(conn, "unowned-but-shared", visibility=speakers.SHARED)
    v = Viewer(None, False)

    with pytest.raises(Forbidden) as clone_exc:
        await voice_access.find_for_clone(conn, "some-name", v)
    with pytest.raises(Forbidden) as write_exc:
        await voice_access.get_for_write(conn, "unowned-but-shared", v)

    assert str(clone_exc.value) == str(write_exc.value)
    assert "belongs to someone else" not in str(write_exc.value)


@pytest.mark.asyncio
async def test_an_ownerless_viewer_may_still_read(conn):
    """Only creation and mutation are refused — an ownerless key may still see what is visible
    to it."""
    await speakers.create_speaker(conn, "public-ish", owner_id=BOB, visibility=speakers.SHARED)
    v = Viewer(None, False)
    speaker = await voice_access.get_for_read(conn, "public-ish", v)
    assert speaker.owner_id == BOB
    assert await voice_access.list_voices(conn, v) != []


@pytest.mark.asyncio
async def test_an_admin_may_read_a_voice_they_do_not_own(conn):
    await speakers.create_speaker(conn, "theirs", owner_id=BOB)  # private
    admin = Viewer(None, True)
    speaker = await voice_access.get_for_read(conn, "theirs", admin)
    assert speaker.owner_id == BOB


def test_no_router_imports_speakers_directly():
    """The defect this module exists to prevent was precisely a route that reached
    `voice_library.speakers` directly and so could not have checked anything. Every router
    must go through `voice_access` instead.
    """
    routers_dir = Path(auris.server.routers.__file__).parent
    offenders = []
    for path in sorted(routers_dir.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # Two forms reach the same module, and checking only `node.module` misses the
                # second — which is the idiom this codebase actually uses:
                #   from auris.voice_library.speakers import x  -> module itself carries "speakers"
                #   from auris.voice_library import speakers    -> "speakers" is an alias, not
                #                                                   part of node.module at all
                if node.module and "speakers" in node.module:
                    offenders.append(f"{path.name}: from {node.module} import ...")
                elif node.module and "voice_library" in node.module:
                    offenders.extend(
                        f"{path.name}: from {node.module} import {alias.name}"
                        for alias in node.names
                        if alias.name == "speakers"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "speakers" in alias.name:
                        offenders.append(f"{path.name}: import {alias.name}")
    assert offenders == [], (
        "a router imports voice_library.speakers directly, bypassing voice_access's permission "
        f"gate: {offenders}"
    )


def test_no_router_calls_may_mutate_directly():
    """`Viewer.may_mutate` is the primitive every permission decision is built from, and the
    rule is that it is only ever called from inside `voice_access` — a route calling it directly
    (as `clone_voice` once did) makes its own ad hoc judgment call instead of going through the
    one gate, which is exactly how the original defect happened.
    """
    routers_dir = Path(auris.server.routers.__file__).parent
    offenders = []
    for path in sorted(routers_dir.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "may_mutate":
                offenders.append(f"{path.name}: .may_mutate(...)")
    assert offenders == [], (
        "a router calls Viewer.may_mutate directly instead of going through voice_access's "
        f"get_for_read/get_for_write/find_for_clone: {offenders}"
    )


def test_no_router_resolves_a_voice_without_a_viewer():
    """Scoping synthesis is only durable if the next route added cannot forget. `resolve_voice`'s
    `viewer=None` branch is the local CLI's unscoped *operator* path — any HTTP route that calls
    it without `viewer=` silently makes every private voice usable by anyone who guesses its
    name, exactly the defect Task 6 closed. A bare `**kwargs` splat does not prove a viewer was
    passed, so it counts as a failure here too, not a pass.
    """
    routers_dir = Path(auris.server.routers.__file__).parent
    offenders = []
    for path in sorted(routers_dir.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            else:
                continue
            if func_name != "resolve_voice":
                continue
            # kw.arg is None for a **kwargs splat, which must not satisfy this check: we need
            # proof a viewer was actually passed, not merely that some mapping might supply one.
            keyword_names = {kw.arg for kw in node.keywords}
            if "viewer" not in keyword_names:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "a router calls resolve_voice without viewer=, so a private voice would be usable by "
        f"anyone who guesses its name: {offenders}"
    )
