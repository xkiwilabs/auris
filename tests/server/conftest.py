from __future__ import annotations

import contextlib
import time

import numpy as np
import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from auris.config import load_settings
from auris.models.base import PreparedReference
from auris.server.app import create_app
from auris.server.asr_pool import TranscriptionPool
from auris.server.auth import auth_dependency, require_admin, require_control_plane
from auris.server.pool import SynthesisPool
from auris.voice_library import db


class FakeModel:
    """A model-free stand-in for tests. Each synthesise returns a marker array whose
    first sample encodes the text length, so callers can assert which chunk they got."""

    sample_rate = 24000

    def __init__(self, device: str = "cpu", delay: float = 0.0, name: str = "f5_tts"):
        self.device = device
        self.delay = delay
        self.name = name
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def synthesise(self, text, reference: PreparedReference, speed: float = 1.0) -> np.ndarray:
        if self.delay:
            time.sleep(self.delay)
        return np.full(8, float(len(text)), dtype=np.float32)


class FakeAsrPool(TranscriptionPool):
    """Model-free stand-in for TranscriptionPool. Returns a fixed Transcript and records
    the jobs it was asked for, so endpoint tests can assert on them.

    It subclasses the real pool so `admit()` — the gate the STT route holds around the
    decode — is the real one, not a stub."""

    def __init__(self, text="hello world", queue_maxsize=8, words=()):
        super().__init__([object()], queue_maxsize, "base.en", "cpu", "int8")
        self.text = text
        self.words = tuple(words)  # word timestamps, for the clone path's cut placement
        self.jobs = []

    async def submit(self, job):
        from auris.audio.transcript import Segment, Transcript

        self.jobs.append(job)
        return Transcript(
            text=self.text,
            segments=(Segment(id=0, start=0.0, end=1.0, text=self.text, words=self.words),),
            language="en",
            language_probability=0.99,
            duration_s=1.0,
        )


def make_reference() -> PreparedReference:
    return PreparedReference(np.zeros(8, np.float32), 24000, "ref text")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fake pool + a tmp WAL DB, bypassing the heavy real lifespan."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AURIS_AUTH_TOKEN", "secret")
    settings = load_settings()

    @contextlib.asynccontextmanager
    async def test_lifespan(app):
        import asyncio

        conn = await db.connect(settings.db_path)
        await db.create_tables(conn)
        app.state.settings = settings
        app.state.db = conn
        app.state.pools = {
            "f5_tts": SynthesisPool([FakeModel()], queue_maxsize=8),
            "kokoro": SynthesisPool([FakeModel(name="kokoro")], queue_maxsize=8),
        }
        app.state.asr_pool = FakeAsrPool()
        app.state.ready = True
        app.state.loop = asyncio.get_running_loop()  # so tests can seed on the right loop
        yield
        await conn.close()

    app = create_app(lifespan=test_lifespan)

    # Test-only probes for the auth dependency (not part of the real API). The imports these
    # need live at module level: this file has `from __future__ import annotations`, so a
    # fixture-local `Request` import would leave FastAPI unable to resolve the annotation and
    # it would treat `request` as a query parameter (→ 422).
    probe = APIRouter(dependencies=[Depends(auth_dependency)])

    @probe.get("/v1/whoami")
    async def _whoami(request: Request):
        p = request.state.principal
        return {
            "principal": p.name,
            "admin": p.admin,
            "kind": p.kind,
            "user_id": p.user_id,
            "must_change_password": p.must_change_password,
        }

    @probe.get("/v1/whoami/admin")
    async def _whoami_admin(_p=Depends(require_admin)):
        return {"ok": True}

    @probe.get("/v1/whoami/control-plane")
    async def _whoami_control(_p=Depends(require_control_plane)):
        return {"ok": True}

    app.include_router(probe)

    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer secret"})
        yield c


def run_on_app_loop(client, coro):
    """Run a coroutine on the app's (lifespan) event loop from the test thread, so DB work
    uses the same loop the aiosqlite connection is bound to."""
    import asyncio

    return asyncio.run_coroutine_threadsafe(coro, client.app.state.loop).result()


GOOD_PASSWORD = "a-good-long-password"


@pytest.fixture
def seed_user(client):
    """Create an account on the app's own event loop. Returns a callable."""
    from auris.voice_library import users

    def _seed(email, password=GOOD_PASSWORD, *, role="user", must_change_password=False):
        return run_on_app_loop(
            client,
            users.create_user(
                client.app.state.db,
                email,
                password,
                role=role,
                must_change_password=must_change_password,
            ),
        )

    return _seed


@pytest.fixture
def as_user(client):
    """Turn the TestClient into a given user's *browser*: session cookie set, bearer removed,
    CSRF header attached. Returns the same client, so `as_user(alice).get("/v1/voices")` reads
    naturally.

    The cookie goes on the client rather than on each request: httpx 0.28 deprecates
    per-request `cookies=`, and 40 warnings would bury a real one.
    """
    from auris.server import session

    def _as(user, *, version=None):
        client.headers.pop("Authorization", None)
        client.headers.update({session.CLIENT_HEADER: session.CLIENT_VALUE})
        client.cookies.clear()
        client.cookies.set(
            session.COOKIE,
            session.issue(
                client.app.state.settings.auth_token,
                session.user_principal(
                    user.id, user.password_version if version is None else version
                ),
                hours=1,
            ),
            # A jar entry with domain="" cannot be matched by the server's host-only
            # Set-Cookie: ...; Max-Age=0, so logout would appear not to work. TestClient's host
            # is "testserver"; the cookiejar suffixes it to "testserver.local", and neither
            # "testserver" nor ".testserver.local" is evicted — this exact spelling is.
            domain="testserver.local",
        )
        return client

    return _as


@pytest.fixture
def seed_voice(client):
    """A cloned voice owned by an account, with one reference clip so it can be synthesised.

    Mirrors `seed_user`: runs on the app's own event loop via `run_on_app_loop`, because the
    aiosqlite connection is bound to that loop. Takes the owner's User object, not their email.
    """
    import numpy as np

    from auris.voice_library import cloning, speakers

    def _seed(name, owner, visibility="private"):
        settings = client.app.state.settings
        conn = client.app.state.db

        async def go():
            sp = await speakers.create_speaker(conn, name, owner_id=owner.id, visibility=visibility)
            audio = np.zeros(settings.sample_rate, dtype=np.float32)
            await cloning.add_reference(
                conn, settings.voices_dir, sp.id, audio, settings.sample_rate, "hello world", 1.0
            )
            return sp

        return run_on_app_loop(client, go())

    return _seed


@pytest.fixture
def as_key(client):
    """Turn the TestClient into a bearer-authenticated API key, owned by `owner` (a `User` from
    `seed_user`) or unowned if `owner` is omitted/None. Returns the same client, mirroring
    `as_user` — `as_key(owner).post(...)` reads naturally.

    Minted straight through `voice_library.keys.create_key`, bypassing `POST /v1/keys`
    entirely: this fixture exists to exercise the *voice* routes as a key principal, not the
    key-issuing route itself (see tests/server/test_keys_routes.py for that).
    """
    import uuid

    from auris.voice_library import keys as keylib

    def _as(owner=None):
        client.cookies.clear()
        name = f"test-key-{uuid.uuid4().hex[:12]}"
        _, plaintext = run_on_app_loop(
            client, keylib.create_key(client.app.state.db, name, owner.id if owner else None)
        )
        client.headers.update({"Authorization": "Bearer " + plaintext})
        return client

    return _as


@pytest.fixture
def as_anon(client):
    """The TestClient with no credential of any kind."""

    def _as():
        client.headers.pop("Authorization", None)
        client.cookies.clear()
        return client

    return _as
