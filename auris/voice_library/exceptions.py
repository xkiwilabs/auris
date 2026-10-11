from __future__ import annotations


class NameTaken(Exception):
    """A name that must be unique (API key, speaker) is already in use (→ 409 conflict)."""


class AmbiguousVoice(Exception):
    """A voice name matches more than one voice the caller can see (→ 409 conflict).

    Two people may each share a voice called `narrator`. Picking one silently would synthesise
    with the wrong voice and give no sign of it, so the caller is asked to disambiguate with
    a disambiguator instead — `owner=<email>` over HTTP, `--owner <email>` from the CLI,
    whichever surface raised it. `owners` carries the matching user ids so the caller can choose —
    kept as opaque ids, not emails, because `voice_library` must not reach into `users` to look
    them up; the server layer maps ids to emails when rendering the error.
    """

    def __init__(self, message: str, owners: tuple[str | None, ...] = ()) -> None:
        super().__init__(message)
        self.owners = owners


class ClipNotFound(LookupError):
    """No reference clip at that index for this voice (→ 404 clip_not_found).

    Distinct from a missing *voice* so a client can tell "no such voice" from "no such clip".
    """


class KeyNotFound(LookupError):
    """No API key by that name (→ 404 key_not_found).

    A `LookupError` subclass so the generic handler still catches it, but distinct enough that
    the error envelope says `key_not_found` rather than the voice routes' `voice_not_found`.
    """


class UserNotFound(LookupError):
    """No account for that email (→ 404 user_not_found).

    A `LookupError` subclass like `KeyNotFound`, for the same reason: the generic handler still
    catches it, but the envelope says `user_not_found` rather than the voice routes'
    `voice_not_found`.
    """


class LastActiveAdmin(Exception):
    """Refused: disabling this account would leave no active admin (→ 409 conflict).

    Enforced inside the UPDATE rather than by a preceding count, so two concurrent disables
    cannot both pass the check and leave the server with no way in.
    """
