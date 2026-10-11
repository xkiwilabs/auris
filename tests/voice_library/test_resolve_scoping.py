from __future__ import annotations

import numpy as np
import pytest
import pytest_asyncio

from auris.config import load_settings
from auris.voice_library import cloning, db, speakers
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.resolve import resolve_voice
from auris.voice_library.viewer import Viewer

ALICE, BOB = "alice-id", "bob-id"


async def _seed_clip(conn, settings, speaker, transcript):
    """Give a speaker a real clip on disk, so resolution can reach load_reference_for_speaker
    and the test can assert on *which* speaker's reference actually got picked."""
    await cloning.add_reference(
        conn,
        settings.voices_dir,
        speaker.id,
        np.zeros(settings.sample_rate, np.float32),
        settings.sample_rate,
        transcript,
        1.0,
    )


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_a_preset_still_wins_the_namespace(conn, monkeypatch, tmp_path):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    r = await resolve_voice(conn, load_settings(), "narrator-us-f", viewer=Viewer(ALICE, False))
    assert r.kind == "preset"


@pytest.mark.asyncio
async def test_another_users_private_voice_is_not_resolvable(conn, monkeypatch, tmp_path):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    await speakers.create_speaker(conn, "secret", owner_id=BOB)
    with pytest.raises(LookupError):
        await resolve_voice(conn, load_settings(), "secret", viewer=Viewer(ALICE, False))


@pytest.mark.asyncio
async def test_two_shared_voices_with_one_name_are_ambiguous(conn, monkeypatch, tmp_path):
    """Synthesising with an arbitrary one of them would be a silent wrong answer."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    await speakers.create_speaker(conn, "narrator", owner_id=ALICE, visibility=speakers.SHARED)
    await speakers.create_speaker(conn, "narrator", owner_id=BOB, visibility=speakers.SHARED)
    with pytest.raises(AmbiguousVoice) as exc:
        await resolve_voice(conn, load_settings(), "narrator", viewer=Viewer("carol", False))
    assert "narrator" in str(exc.value)
    # The count alone isn't enough to disambiguate — the caller needs to know *who* owns each
    # match, carried on the exception rather than parsed out of the message text.
    assert set(exc.value.owners) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_viewers_own_match_wins_over_a_same_named_shared_voice(conn, monkeypatch, tmp_path):
    """Alice's own 'narrator' must resolve to HER voice even after Bob shares one with the same
    name — otherwise anyone could break another user's `--voice <name>` just by sharing a voice
    under that name. Asserting on the resolved transcript, not just "no exception", because a
    wrong-but-successful resolution is exactly the silent failure mode this guards against."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    alice_sp = await speakers.create_speaker(conn, "narrator", owner_id=ALICE)
    await _seed_clip(conn, settings, alice_sp, "alice's own line")
    bob_sp = await speakers.create_speaker(
        conn, "narrator", owner_id=BOB, visibility=speakers.SHARED
    )
    await _seed_clip(conn, settings, bob_sp, "bob's shared line")

    r = await resolve_voice(conn, settings, "narrator", viewer=Viewer(ALICE, False))

    assert r.conditioning.transcript == "alice's own line"


@pytest.mark.asyncio
async def test_ambiguous_message_names_no_owner_ids(conn, monkeypatch, tmp_path):
    """The message is reachable over HTTP via the three synthesis routes, which accept an email
    (not a user id) as `owner`; it must not leak either owner's internal id to a caller who can
    merely see that two people share a voice name."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    await speakers.create_speaker(conn, "narrator", owner_id=ALICE, visibility=speakers.SHARED)
    await speakers.create_speaker(conn, "narrator", owner_id=BOB, visibility=speakers.SHARED)
    with pytest.raises(AmbiguousVoice) as exc:
        await resolve_voice(conn, load_settings(), "narrator", viewer=Viewer("carol", False))
    message = str(exc.value)
    assert "owner=<email>" in message
    assert ALICE not in message
    assert BOB not in message


@pytest.mark.asyncio
async def test_unscoped_operator_path_honours_owner_with_no_viewer(conn, monkeypatch, tmp_path):
    """`viewer=None` is the local CLI's unscoped operator path (Task 7's `auris voices --owner
    <email>`); there `owner` is a plain disambiguator, not a security boundary, so it must still
    reach a private voice with no viewer at all. Pinned deliberately so this doesn't get "fixed"
    back into a security check and break that CLI path."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    sp = await speakers.create_speaker(conn, "secret", owner_id=BOB)
    await _seed_clip(conn, settings, sp, "bob's secret line")

    r = await resolve_voice(conn, settings, "secret", owner=BOB)

    assert r.conditioning.transcript == "bob's secret line"
