"""The one way a router reaches a voice.

Every permission rule for voices lives here. Routers must not import `voice_library.speakers`
directly — the defect this module exists to prevent was precisely a route (`delete_voice`) that
took no principal and so could not have checked anything.
"""

from __future__ import annotations

import aiosqlite

from auris.server.auth import Principal
from auris.server.errors import Forbidden
from auris.voice_library import speakers, users
from auris.voice_library.exceptions import AmbiguousVoice
from auris.voice_library.speakers import Speaker
from auris.voice_library.viewer import Viewer

# Returned by owner_id_for for an email with no account. Matches no real speakers.owner_id, so
# a lookup narrowed to it answers 404 like any other invisible voice, never 422 — the caller
# must not learn which addresses have accounts.
_NO_SUCH_OWNER = "\x00no-such-owner"


def viewer_for(principal: Principal) -> Viewer:
    """`Principal` already carries the right account: `resolve_bearer` sets `user_id` to a
    key's *owner*, and `admin` is hardcoded False for keys. So this is a direct mapping, and
    the master token falls out as an admin with no account."""
    return Viewer(principal.user_id, principal.admin)


async def owner_id_for(conn: aiosqlite.Connection, owner_email: str | None) -> str | None:
    """Translate a caller-supplied `?owner=`/`owner=` email into a user id, for narrowing
    `get_for_read`/`get_for_write`/`resolve_voice`'s visible-match set to one owner. An unknown
    address resolves to `_NO_SUCH_OWNER`, a sentinel that matches nothing, so it comes back 404
    like any other invisible voice rather than 422 — see the constant's comment.
    """
    if owner_email is None:
        return None
    u = await users.get_user_by_email(conn, owner_email)
    return u.id if u else _NO_SUCH_OWNER


async def list_voices(
    conn: aiosqlite.Connection, viewer: Viewer
) -> list[tuple[str, int, float, str, str | None, str]]:
    return await speakers.list_visible_with_stats(conn, viewer)


async def get_for_read(
    conn: aiosqlite.Connection, name: str, viewer: Viewer, owner: str | None = None
) -> Speaker:
    """The voice, if this viewer may see it.

    Raises LookupError (→ 404) when it does not exist *or* is not visible — never Forbidden,
    which would confirm the name is taken.
    """
    matches = await speakers.visible_speakers_by_name(conn, name, viewer)
    if owner is not None:
        matches = [s for s in matches if s.owner_id == owner]
    if not matches:
        raise LookupError(f"voice {name!r} not found")
    if len(matches) > 1:
        owners = tuple(s.owner_id for s in matches)
        # A bare count, not the owner ids or emails: this message is the one place AmbiguousVoice
        # is reachable over HTTP, so it must not leak who else has a voice by this name. `owner=`
        # names the query parameter the route actually accepts (an email, resolved by
        # `_owner_id_for`) — the structured `owners` attribute still carries the ids, for a
        # programmatic caller, never for display.
        raise AmbiguousVoice(
            f"several voices named {name!r} are visible to you ({len(matches)}); "
            "pass owner=<email> to choose one",
            owners=owners,
        )
    return matches[0]


def refuse_if_ownerless(viewer: Viewer) -> None:
    """Raise Forbidden for a viewer with no account and no admin rights (an ownerless API key):
    it owns nothing, so it cannot create or modify any voice, ever — not even one that is
    itself unowned, which `may_mutate` already refuses, but with a message that is literally
    false for that case ("belongs to someone else" when it belongs to no one).

    One implementation, used by both `find_for_clone` and `get_for_write`, and called directly
    by `clone_voice` before any audio work — see its call site for why. Takes no voice and does
    no lookup, so the refusal is identical regardless of what the caller was trying to touch,
    the same discipline `find_for_clone` already applied to its own call of this check.
    """
    if viewer.user_id is None and not viewer.is_admin:
        raise Forbidden(
            "this API key has no owner account, so it cannot create or modify voices; "
            "ask an admin to re-issue it for an account"
        )


async def get_for_write(
    conn: aiosqlite.Connection, name: str, viewer: Viewer, owner: str | None = None
) -> Speaker:
    """The voice, if this viewer may modify it.

    An ownerless, non-admin viewer is refused first, before any lookup — see
    `refuse_if_ownerless` — so PATCH and DELETE report the same actionable cause `find_for_clone`
    already does, instead of "belongs to someone else", which is untrue for an unowned voice and
    uninformative for anyone else's.

    Visibility is checked next, so someone else's *private* voice is a 404 and someone else's
    *shared* voice is a 403. The order matters: swapping it would leak the existence of every
    private voice through the status code.
    """
    refuse_if_ownerless(viewer)
    speaker = await get_for_read(conn, name, viewer, owner)
    if not viewer.may_mutate(speaker.owner_id):
        raise Forbidden(f"voice {name!r} belongs to someone else")
    return speaker


async def find_for_clone(conn: aiosqlite.Connection, name: str, viewer: Viewer) -> Speaker | None:
    """The voice this clone would append to, or None if it would create a new one.

    Returns the viewer's own voice in preference to a shared one, so cloning `narrator` when
    someone else shares `narrator` creates *your* `narrator` rather than touching theirs. An
    ambiguous shared name (two owners both share `narrator`) is also None here: `clone_voice`
    then creates the caller's own, same as if no match existed at all, rather than guessing.

    Raises Forbidden when a single visible match exists but the viewer may not write it —
    appending a clip to a voice is a mutation, and the permission decision belongs here, not in
    the router: every rule for who may touch a voice lives in this module and nowhere else.

    Also calls `refuse_if_ownerless` first, before any lookup, for the same reason documented
    there. `clone_voice` calls it again even earlier, before the whisper decode — this call
    stays so `find_for_clone` is safe to call on its own, from a test or any future caller,
    without relying on the router to have checked first.
    """
    refuse_if_ownerless(viewer)
    own = await speakers.get_speaker_by_name_and_owner(conn, name, viewer.user_id)
    if own is not None:
        return own
    matches = await speakers.visible_speakers_by_name(conn, name, viewer)
    existing = matches[0] if len(matches) == 1 else None
    if existing is not None and not viewer.may_mutate(existing.owner_id):
        raise Forbidden(f"voice {name!r} belongs to someone else")
    return existing


# Thin pass-throughs for the row operations the router still needs, so the router's only
# dependency on voice persistence is this module — never `voice_library.speakers` directly.
# They add nothing but delegation; the permission checks all happen above, in get_for_read/write.


async def set_visibility(conn: aiosqlite.Connection, speaker: Speaker, visibility: str) -> Speaker:
    """Change a voice's visibility, refusing the one change that would be inert.

    Takes the `Speaker` the caller already resolved rather than an id, because the refusal needs
    `owner_id`: an unowned voice is visible to every account whatever its column says, so
    `private` there is a no-op (`speakers.refuse_inert_private`, ValueError → 422). The rule
    lives on this side of the wire so every API client inherits it — the CLI's remote `unshare`
    used to report success for a change the server quietly did not make, while the same command
    on the box refused.
    """
    speakers.refuse_inert_private(speaker, visibility)
    return await speakers.set_visibility(conn, speaker.id, visibility)


async def rename(conn: aiosqlite.Connection, speaker_id: str, new_name: str) -> Speaker:
    return await speakers.rename_speaker(conn, speaker_id, new_name)


async def get_clips(conn: aiosqlite.Connection, speaker_id: str):
    return await speakers.get_clips(conn, speaker_id)


async def delete_voice(conn: aiosqlite.Connection, speaker_id: str) -> None:
    await speakers.delete_speaker(conn, speaker_id)


async def get_clip_by_filename(conn: aiosqlite.Connection, speaker_id: str, filename: str):
    return await speakers.get_clip_by_filename(conn, speaker_id, filename)


async def delete_clip_if_not_last(
    conn: aiosqlite.Connection, speaker_id: str, clip_id: str
) -> bool:
    return await speakers.delete_clip_if_not_last(conn, speaker_id, clip_id)
