"""Who is looking at the voice library.

Deliberately import-light — the CLI reaches this module, and `auris/cli/` may not pull in
fastapi (see tests/cli/test_import_seam.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Viewer:
    """The account a request acts as.

    `user_id` is None for the master token (an admin with no account) and for a pre-accounts
    API key (owns nothing, and is NOT an admin). Those two are told apart by `is_admin`, never
    by `user_id` alone — reading "no user_id" as "unscoped" would hand a legacy key the run of
    the library. Unscoped access is expressed by passing no Viewer at all, which only the local
    CLI on the box does.
    """

    user_id: str | None
    is_admin: bool

    def may_mutate(self, owner_id: str | None) -> bool:
        """Whether this viewer may rename, delete or re-share a voice owned by `owner_id`.

        A legacy voice (owner_id None) is admin-only: nobody can prove they created it, so
        letting the first caller claim it by mutating it would be a land grab.
        """
        if self.is_admin:
            return True
        return owner_id is not None and owner_id == self.user_id
