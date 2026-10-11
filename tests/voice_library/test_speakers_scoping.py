from __future__ import annotations

import pytest
import pytest_asyncio

from auris.voice_library import db, speakers
from auris.voice_library.exceptions import NameTaken
from auris.voice_library.viewer import Viewer

ALICE, BOB = "alice-id", "bob-id"
AS_ALICE = Viewer(ALICE, False)
AS_BOB = Viewer(BOB, False)
AS_ADMIN = Viewer("admin-id", True)
AS_MASTER = Viewer(None, True)
AS_LEGACY_KEY = Viewer(None, False)  # a pre-accounts API key: owns nothing, is not admin


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


async def _seed(conn):
    await speakers.create_speaker(conn, "alice-private", owner_id=ALICE)
    await speakers.create_speaker(conn, "alice-shared", owner_id=ALICE, visibility=speakers.SHARED)
    await speakers.create_speaker(conn, "bob-private", owner_id=BOB)
    # A legacy row, as the migration leaves them.
    await conn.execute(
        "INSERT INTO speakers (id, name, created_at, owner_id, visibility)"
        " VALUES ('legacy', 'old-voice', 'then', NULL, 'shared')"
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_a_new_voice_is_private_and_owned_by_its_creator(conn):
    sp = await speakers.create_speaker(conn, "mine", owner_id=ALICE)
    assert sp.owner_id == ALICE
    assert sp.visibility == speakers.PRIVATE


@pytest.mark.asyncio
async def test_a_voice_created_with_no_owner_is_shared_not_private(conn):
    """`auris clone`/`auris record` on the box create unowned rows. `_VISIBLE`'s
    `s.owner_id IS NULL` clause shows those to every account whatever the column says, so a
    stored `private` would be a lie the operator is then shown by `auris voices`, the dashboard
    and `GET /v1/voices` — see speakers.UNOWNED_IS_ALWAYS_SHARED."""
    sp = await speakers.create_speaker(conn, "box-created")
    assert sp.owner_id is None
    assert sp.visibility == speakers.SHARED
    # The column *and* the access it claims to describe: an unrelated non-admin sees the voice.
    rows = await speakers.list_visible_with_stats(conn, AS_BOB)
    assert [(r[0], r[5]) for r in rows] == [("box-created", speakers.SHARED)]


@pytest.mark.asyncio
async def test_creating_an_unowned_voice_as_private_is_refused(conn):
    """The create-side half of the rule `refuse_inert_private` already enforced on updates: we
    refuse rather than coerce, so a caller never gets success for a visibility we did not
    store. The derived default means no honest caller needs to pass this at all."""
    with pytest.raises(ValueError, match="no owner"):
        await speakers.create_speaker(conn, "dishonest", visibility=speakers.PRIVATE)
    assert await speakers.get_speaker_by_name_and_owner(conn, "dishonest", None) is None
    # An owned row may still be created private explicitly.
    owned = await speakers.create_speaker(conn, "mine", owner_id=ALICE, visibility=speakers.PRIVATE)
    assert owned.visibility == speakers.PRIVATE


@pytest.mark.asyncio
async def test_adopting_a_box_created_voice_does_not_change_who_can_see_it(conn):
    """The regression that matters most. `auris voices adopt` is on the documented deploy path,
    and with a box-created row stored as `private` it silently revoked every other account's
    access: unowned+private is readable by all, owned+private by nobody else."""
    await speakers.create_speaker(conn, "operator-voice")
    before = [r[0] for r in await speakers.list_visible_with_stats(conn, AS_BOB)]
    assert before == ["operator-voice"]

    assert await speakers.adopt_unowned(conn, ALICE) == 1

    after = [r[0] for r in await speakers.list_visible_with_stats(conn, AS_BOB)]
    assert after == before, "adopt must not change who can see the voice"
    adopted = await speakers.get_speaker_by_name_and_owner(conn, "operator-voice", ALICE)
    assert adopted is not None and adopted.visibility == speakers.SHARED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewer,expected",
    [
        (AS_ALICE, ["alice-private", "alice-shared", "old-voice"]),
        (AS_BOB, ["alice-shared", "bob-private", "old-voice"]),
        (AS_ADMIN, ["alice-private", "alice-shared", "bob-private", "old-voice"]),
        (AS_MASTER, ["alice-private", "alice-shared", "bob-private", "old-voice"]),
        (AS_LEGACY_KEY, ["alice-shared", "old-voice"]),
    ],
    ids=["alice", "bob", "admin", "master", "legacy-key"],
)
async def test_listing_is_scoped_to_what_the_viewer_may_see(conn, viewer, expected):
    await _seed(conn)
    rows = await speakers.list_visible_with_stats(conn, viewer)
    assert sorted(r[0] for r in rows) == sorted(expected)


@pytest.mark.asyncio
async def test_another_users_private_voice_is_simply_absent(conn):
    await _seed(conn)
    assert await speakers.visible_speakers_by_name(conn, "bob-private", AS_ALICE) == []
    found = await speakers.visible_speakers_by_name(conn, "bob-private", AS_BOB)
    assert [s.name for s in found] == ["bob-private"]


@pytest.mark.asyncio
async def test_two_owners_sharing_a_name_both_come_back(conn):
    """The caller has to be told it is ambiguous; picking one silently is the bad outcome."""
    await speakers.create_speaker(conn, "narrator", owner_id=ALICE, visibility=speakers.SHARED)
    await speakers.create_speaker(conn, "narrator", owner_id=BOB, visibility=speakers.SHARED)
    found = await speakers.visible_speakers_by_name(conn, "narrator", Viewer("carol", False))
    assert len(found) == 2


@pytest.mark.asyncio
async def test_your_own_voice_sorts_ahead_of_a_shared_one_with_the_same_name(conn):
    """Own-before-shared: nobody can shadow your voice by sharing one with the same name."""
    await speakers.create_speaker(conn, "narrator", owner_id=ALICE)
    await speakers.create_speaker(conn, "narrator", owner_id=BOB, visibility=speakers.SHARED)
    found = await speakers.visible_speakers_by_name(conn, "narrator", AS_ALICE)
    assert [s.owner_id for s in found] == [ALICE, BOB]


@pytest.mark.asyncio
async def test_renaming_onto_a_name_another_owner_uses_is_allowed_now(conn):
    """Regression guard for the index swap: this raised NameTaken under the global index."""
    await speakers.create_speaker(conn, "narrator", owner_id=BOB)
    mine = await speakers.create_speaker(conn, "mine", owner_id=ALICE)
    renamed = await speakers.rename_speaker(conn, mine.id, "narrator")
    assert renamed.name == "narrator"
    # Identity, not just name: a by-name re-fetch here could resolve to Bob's "narrator" instead
    # of Alice's renamed voice — same name, wrong row, wrong owner.
    assert renamed.id == mine.id
    assert renamed.owner_id == ALICE


@pytest.mark.asyncio
async def test_renaming_onto_your_own_existing_name_is_still_refused(conn):
    from auris.voice_library.exceptions import NameTaken

    await speakers.create_speaker(conn, "taken", owner_id=ALICE)
    other = await speakers.create_speaker(conn, "other", owner_id=ALICE)
    with pytest.raises(NameTaken):
        await speakers.rename_speaker(conn, other.id, "TAKEN")


@pytest.mark.asyncio
async def test_may_mutate_follows_the_matrix(conn):
    assert AS_ALICE.may_mutate(ALICE) is True
    assert AS_ALICE.may_mutate(BOB) is False
    assert AS_ALICE.may_mutate(None) is False  # legacy rows are admin-only
    assert AS_ADMIN.may_mutate(BOB) is True
    assert AS_ADMIN.may_mutate(None) is True
    assert AS_MASTER.may_mutate(BOB) is True
    assert AS_LEGACY_KEY.may_mutate(None) is False


@pytest.mark.asyncio
async def test_set_visibility_round_trips_and_rejects_rubbish(conn):
    sp = await speakers.create_speaker(conn, "v", owner_id=ALICE)
    assert (await speakers.set_visibility(conn, sp.id, speakers.SHARED)).visibility == "shared"
    with pytest.raises(ValueError):
        await speakers.set_visibility(conn, sp.id, "public")


@pytest.mark.asyncio
async def test_adopt_claims_only_unowned_voices(conn):
    await _seed(conn)
    assert await speakers.adopt_unowned(conn, ALICE) == 1
    adopted = await speakers.get_speaker_by_name_and_owner(conn, "old-voice", ALICE)
    assert adopted is not None
    assert await speakers.adopt_unowned(conn, ALICE) == 0  # idempotent
    bobs = await speakers.visible_speakers_by_name(conn, "bob-private", AS_BOB)
    assert bobs[0].owner_id == BOB  # untouched


@pytest.mark.asyncio
async def test_adopt_refuses_a_name_the_account_already_owns_and_claims_nothing(conn):
    """The expected state of a box that has both legacy voices and accounts: Alice created her
    own `old-voice` before remembering to adopt. SQLite aborts the whole UPDATE, so adopt is
    all-or-nothing — `NameTaken` names the clash and every row keeps its owner."""
    await _seed(conn)
    await speakers.create_speaker(conn, "old-voice", owner_id=ALICE)
    with pytest.raises(NameTaken) as exc:
        await speakers.adopt_unowned(conn, ALICE)
    assert "old-voice" in str(exc.value)
    legacy = await speakers.get_speaker_by_name_and_owner(conn, "old-voice", None)
    assert legacy is not None and legacy.owner_id is None  # nothing was claimed


@pytest.mark.asyncio
async def test_a_refused_adopt_leaves_the_connection_writable(conn):
    """The rollback matters, not just the exception: the aborted UPDATE leaves the implicit
    transaction open on a connection the server shares, so the next writer would block on the
    WAL write lock. Asserting a later write commits is the observable form of that."""
    await _seed(conn)
    await speakers.create_speaker(conn, "old-voice", owner_id=ALICE)
    with pytest.raises(NameTaken):
        await speakers.adopt_unowned(conn, ALICE)
    assert await speakers.adopt_unowned(conn, BOB) == 1  # the clash was Alice's alone
    assert conn.in_transaction is False


@pytest.mark.asyncio
async def test_an_unowned_voice_cannot_be_made_private(conn):
    """Shared by `auris voices unshare` and `PATCH /v1/voices/{name}` — the rule is one
    function so the box and the service cannot answer it differently."""
    sp = await speakers.create_speaker(conn, "legacy", owner_id=None)
    with pytest.raises(ValueError, match="no owner"):
        speakers.refuse_inert_private(sp, speakers.PRIVATE)
    speakers.refuse_inert_private(sp, speakers.SHARED)  # sharing one is allowed, with a note
    owned = await speakers.create_speaker(conn, "mine", owner_id=ALICE)
    speakers.refuse_inert_private(owned, speakers.PRIVATE)  # an owned voice is unaffected
