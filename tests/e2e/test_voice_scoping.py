# tests/e2e/test_voice_scoping.py
"""Voice ownership and visibility, over real HTTP with real cookies and real bearer tokens.

Marked e2e only — never requires_model. This is the tier that proves the scoping holds through
the real middleware/dependency chain, not just a TestClient: `tests/e2e/server.py` seeds
`admin_voice`, owned by the admin account and left `private` (new voices default to private).

Tests run in file order (no randomisation plugin is installed) and some deliberately build on
state the previous test left behind — `admin_voice` starts private and is shared partway
through, exactly so a later test can exercise "shared but still not writable" against it.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest

from tests.e2e.audio import REFERENCE_TEXT, reference_wav
from tests.e2e.server import Instance
from tests.e2e.test_accounts import _login

pytestmark = pytest.mark.e2e


def test_a_user_cannot_see_or_touch_an_admins_private_voice(server: Instance):
    with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
        names = [v["name"] for v in c.get("/v1/voices").json()]
        assert server.admin_voice not in names
        assert c.patch(f"/v1/voices/{server.admin_voice}", json={"name": "x"}).status_code == 404
        assert c.delete(f"/v1/voices/{server.admin_voice}").status_code == 404
        r = c.post("/v1/tts", json={"text": "hello", "voice": server.admin_voice})
        assert r.status_code == 404


def test_an_api_key_owned_by_the_user_cannot_see_the_admins_private_voice(server: Instance):
    """The key is issued by the signed-in user account, so it must see exactly what that
    account sees over the voice routes — not what the admin can see."""
    with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
        issued = c.post("/v1/keys", json={"name": "e2e-scope-read"})
        assert issued.status_code == 201
        key = issued.json()["key"]
    try:
        with httpx.Client(base_url=server.base_url, timeout=60.0) as bot:
            headers = {"Authorization": f"Bearer {key}"}
            names = [v["name"] for v in bot.get("/v1/voices", headers=headers).json()]
            assert server.admin_voice not in names
    finally:
        with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
            c.delete("/v1/keys/e2e-scope-read")


def test_sharing_makes_it_visible_but_still_not_writable(server: Instance):
    with contextlib.closing(_login(server, server.admin_email, server.admin_password)) as c:
        assert (
            c.patch(f"/v1/voices/{server.admin_voice}", json={"visibility": "shared"}).status_code
            == 200
        )
    with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
        assert server.admin_voice in [v["name"] for v in c.get("/v1/voices").json()]
        assert (
            c.post("/v1/tts", json={"text": "hello", "voice": server.admin_voice}).status_code
            == 200
        )
        assert c.delete(f"/v1/voices/{server.admin_voice}").status_code == 403


def test_a_key_owned_by_the_user_can_write_its_own_voice_but_not_the_shared_admin_voice(
    server: Instance,
):
    """The Task 5 review found zero e2e coverage of a `key` principal performing a WRITE on the
    voice routes. A key owned by the user account may clone-create and clone-append its own
    voice, but still only gets 403 against the admin's voice — shared by the previous test, so
    visible, but owned by someone else."""
    with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
        issued = c.post("/v1/keys", json={"name": "e2e-scope-write"})
        assert issued.status_code == 201
        key = issued.json()["key"]
    voice = "e2e-key-owned-voice"
    try:
        wav, _ = reference_wav()
        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(base_url=server.base_url, timeout=60.0) as bot:
            first = bot.post(
                f"/v1/voices/{voice}/clone",
                headers=headers,
                files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
                data={"ref_text": REFERENCE_TEXT},
            )
            assert first.status_code == 201
            second = bot.post(
                f"/v1/voices/{voice}/clone",
                headers=headers,
                files={"file": ("ref.wav", wav.read_bytes(), "audio/wav")},
                data={"ref_text": REFERENCE_TEXT},
            )
            assert second.status_code == 201
            clips = bot.get(f"/v1/voices/{voice}/clips", headers=headers)
            assert clips.status_code == 200
            assert len(clips.json()) == 2

            assert (
                bot.patch(
                    f"/v1/voices/{server.admin_voice}", headers=headers, json={"name": "nope"}
                ).status_code
                == 403
            )
            assert (
                bot.delete(f"/v1/voices/{server.admin_voice}", headers=headers).status_code == 403
            )
    finally:
        with contextlib.closing(_login(server, server.user_email, server.user_password)) as c:
            c.delete(f"/v1/voices/{voice}")
            c.delete("/v1/keys/e2e-scope-write")
