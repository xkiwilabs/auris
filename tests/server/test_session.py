from __future__ import annotations

import contextlib
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from auris.config import load_settings
from auris.server import session
from auris.server.app import create_app
from auris.voice_library import db, keys, users

from .conftest import GOOD_PASSWORD

CSRF = {"X-Auris-Client": "dashboard"}


# ---- pure functions ----


def test_issue_and_verify_roundtrip():
    v = session.issue("master", "alice", hours=1, now=1_000.0)
    assert v.startswith("v1|")
    assert session.verify("master", v, now=1_500.0) == "alice"


def test_expired_session_does_not_verify():
    v = session.issue("master", "alice", hours=1, now=1_000.0)
    assert session.verify("master", v, now=1_000.0 + 3601) is None
    assert session.is_expired(v, now=1_000.0 + 3601) is True
    assert session.is_expired(v, now=1_500.0) is False


def test_tampered_or_foreign_session_does_not_verify():
    v = session.issue("master", "alice", hours=1, now=1_000.0)
    assert session.verify("master", v.replace("alice", "admin"), now=1_500.0) is None
    assert session.verify("rotated-master", v, now=1_500.0) is None  # token rotation logs out
    assert session.verify("master", "garbage", now=1_500.0) is None
    assert session.verify("master", "", now=1_500.0) is None
    assert session.verify("master", v + "|extra", now=1_500.0) is None


def test_a_signed_but_malformed_expiry_does_not_verify():
    # Reached only with a valid signature, so sign it the way the module would.
    payload = "v1|not-a-number|alice"
    signed = f"{payload}|{session._sign('master', payload)}"
    assert session.verify("master", signed, now=1_500.0) is None
    assert session.is_expired(signed, now=1_500.0) is False  # unknown expiry is not "expired"


def test_issue_refuses_an_empty_master():
    # sha256("auris-session:") is a publicly derivable constant, so an empty master would
    # let anyone forge an admin cookie. Refuse rather than sign with a known key.
    with pytest.raises(ValueError):
        session.issue("", "admin", hours=1)
    with pytest.raises(ValueError):
        session.issue("   ", "admin", hours=1)


def test_verify_never_accepts_anything_under_an_empty_master():
    # Forge the cookie exactly as an attacker could, from hashlib/hmac alone.
    forged_key = hashlib.sha256(b"auris-session:").digest()
    payload = "v1|99999999999|admin"
    sig = hmac.new(forged_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    assert session.verify("", f"{payload}|{sig}", now=1_500.0) is None
    assert session.verify("   ", f"{payload}|{sig}", now=1_500.0) is None


def test_expires_at_reads_the_payload():
    v = session.issue("master", "alice", hours=1, now=1_000.0)
    assert session.expires_at(v) == 4600
    assert session.expires_at("garbage") is None
    assert session.expires_at("v1|nope|alice|sig") is None


def test_user_principal_round_trips():
    p = session.user_principal("abc123", 7)
    assert p == "u:abc123:7"
    assert session.parse_user_principal(p) == ("abc123", 7)


def test_a_user_principal_survives_issue_and_verify():
    value = session.issue("master-token", session.user_principal("abc123", 7), hours=1)
    assert session.parse_user_principal(session.verify("master-token", value)) == ("abc123", 7)


@pytest.mark.parametrize(
    "principal",
    ["", "admin", "ci-bot", "u:", "u:abc123", "u:abc123:notanint", "u:abc123:7:8", "x:abc123:7"],
)
def test_anything_that_is_not_a_user_principal_parses_to_none(principal):
    """A key-name or master-token cookie from before this change must not resolve to a user."""
    assert session.parse_user_principal(principal) is None


def test_a_user_id_containing_the_payload_delimiter_is_refused():
    """The cookie payload is pipe-delimited; a principal with a pipe would forge fields."""
    with pytest.raises(ValueError, match="delimiter"):
        session.user_principal("abc|123", 1)


# ---- HTTP ----


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """A TestClient with NO default Authorization header — it must sign in like a browser."""
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
        app.state.pools = {}
        app.state.asr_pool = None
        app.state.ready = True
        app.state.loop = asyncio.get_running_loop()  # so tests can seed on the right loop
        yield
        await conn.close()

    app = create_app(lifespan=test_lifespan)
    with TestClient(app) as c:
        yield c


def _seed(browser, email="alice@example.com", password=GOOD_PASSWORD, **kw):
    """Create an account on the browser app's own event loop."""
    from tests.server.conftest import run_on_app_loop

    return run_on_app_loop(browser, users.create_user(browser.app.state.db, email, password, **kw))


def _sign_in(browser, email="alice@example.com", password=GOOD_PASSWORD):
    return browser.post("/auth/login", json={"email": email, "password": password})


def test_login_sets_a_hardened_cookie(browser):
    _seed(browser)
    r = _sign_in(browser)
    assert r.status_code == 200, r.text
    assert r.json()["principal"] == "alice@example.com" and r.json()["admin"] is False
    assert r.json()["expires"] > 0
    raw = r.headers["set-cookie"]
    assert raw.startswith("auris_session=")
    assert "HttpOnly" in raw
    assert "samesite=strict" in raw.lower()
    assert "Path=/" in raw
    assert "Secure" not in raw  # plain http in tests / on the tailnet

    # A real user session now does authenticate /auth/me.
    r = browser.get("/auth/me", headers=CSRF)
    assert r.status_code == 200


def test_login_over_https_marks_the_cookie_secure(browser):
    _seed(browser)
    r = browser.post(
        "https://testserver/auth/login",
        json={"email": "alice@example.com", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert "Secure" in r.headers["set-cookie"]


def test_cookie_without_csrf_header_is_401(browser):
    _seed(browser)
    _sign_in(browser)
    r = browser.get("/v1/voices")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert "X-Auris-Client" in r.json()["error"]["message"]


def test_csrf_header_is_required_outside_v1_and_on_reads(browser):
    """A deliberate deviation from the spec's "/v1/* only": the header is required on every
    cookie-authenticated request, mutating or not, so no cookie route is left uncovered."""
    _seed(browser)
    _sign_in(browser)
    assert browser.get("/auth/me").status_code == 401  # a GET, and not under /v1
    assert browser.post("/auth/logout").status_code == 401
    assert "X-Auris-Client" in browser.get("/auth/me").json()["error"]["message"]
    # wrong value is no better than none
    assert browser.get("/auth/me", headers={"X-Auris-Client": "curl"}).status_code == 401


def test_bearer_needs_no_csrf_header(browser):
    r = browser.get("/v1/voices", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_no_credentials_at_all_is_401(browser):
    r = browser.get("/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_a_failed_login_is_401_and_sets_no_cookie(browser):
    _seed(browser)
    r = browser.post(
        "/auth/login", json={"email": "alice@example.com", "password": "wrong-password-here"}
    )
    assert r.status_code == 401
    # The same envelope every other 401 uses — the login failure path must not invent its own.
    assert r.json() == {
        "error": {"code": "unauthorized", "message": "email or password not accepted"}
    }
    assert "set-cookie" not in r.headers
    assert browser.get("/auth/me", headers=CSRF).status_code == 401


def test_logout_clears_the_cookie(client, seed_user, as_user):
    """Rewritten for Task 5 against a real user session — `/auth/login` itself only grows an
    email+password mode in Task 7, and a key-based login cookie can no longer reach an
    authenticated route at all (see test_a_key_login_cookie_no_longer_authenticates_v1_routes),
    so it can't reach /auth/logout either.

    `as_user`'s cookie now carries the exact domain the server's own Set-Cookie deletion can
    evict (see the fixture), so the round trip is asserted directly, plus the Max-Age=0 header
    the server sends on logout."""
    alice = seed_user("alice@example.com")
    browser = as_user(alice)
    assert browser.get("/v1/whoami").status_code == 200
    r = browser.post("/auth/logout", headers=CSRF)
    assert r.status_code == 200
    raw = r.headers["set-cookie"]
    assert raw.startswith("auris_session=") and "Max-Age=0" in raw
    assert browser.get("/v1/whoami").status_code == 401


def test_rotating_the_master_token_kills_every_session(client, seed_user, as_user):
    """Rewritten for Task 5 against a real user session, since a key/master-token login cookie
    no longer authenticates anything to rotate away from (see the tests above)."""
    alice = seed_user("alice@example.com")
    browser = as_user(alice)
    assert browser.get("/auth/me", headers=CSRF).status_code == 200
    # `token.sh rotate` + restart, in effect: the HMAC key is derived from the master token.
    browser.app.state.settings.auth_token = "rotated"
    assert browser.get("/auth/me", headers=CSRF).status_code == 401


def test_expired_cookie_is_401(browser):
    # Inject an already-expired (but correctly signed) cookie instead of moving the clock:
    # patching time.time() globally would also make httpx's cookie jar drop the cookie before
    # the request, and the server would answer "signed out" — the test would pass for the
    # wrong reason.
    browser.cookies.set(session.COOKIE, session.issue("secret", "admin", hours=-1))
    r = browser.get("/auth/me", headers=CSRF)
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]


def test_a_tampered_cookie_cannot_escalate_to_admin(browser):
    carol = _seed(browser, "carol@example.com")
    _sign_in(browser, "carol@example.com")
    raw = browser.cookies[session.COOKIE]
    browser.cookies.set(session.COOKIE, raw.replace(carol.id, "admin"))
    r = browser.get("/auth/me", headers=CSRF)
    assert r.status_code == 401


def test_session_hours_setting_is_read(monkeypatch):
    monkeypatch.setenv("AURIS_SESSION_HOURS", "2.5")
    assert load_settings().session_hours == 2.5
    monkeypatch.delenv("AURIS_SESSION_HOURS")
    assert load_settings().session_hours == 12.0


# ---- hostile input (regression: a bad cookie must never 500) ----


@pytest.mark.parametrize(
    "sig",
    ["\xe4", "é", "\xe4" * 64, "ok-ascii-but-wrong"],
    ids=["latin1-byte", "accented", "long-non-ascii", "ascii-control"],
)
def test_verify_is_total_for_a_non_ascii_signature(sig):
    # hmac.compare_digest REFUSES str arguments holding non-ASCII and raises TypeError.
    # The cookie is attacker-controlled, so verify() must return None, never raise.
    assert session.verify("master", f"v1|9999999999|alice|{sig}") is None


def test_a_non_ascii_cookie_is_401_not_500(browser):
    # Starlette latin-1-decodes header bytes, so a high byte in the signature field arrives as
    # a non-ASCII str. httpx's str header API rejects such a cookie, hence the byte tuples --
    # which is exactly why this class of input was not otherwise reachable from a test.
    headers = [
        (b"cookie", "auris_session=v1|9999999999|alice|\xe4".encode("latin-1")),
        (b"x-auris-client", b"dashboard"),
    ]
    assert browser.get("/auth/me", headers=headers).status_code == 401
    assert browser.get("/v1/voices", headers=headers).status_code == 401


def test_an_oversized_login_body_is_rejected_before_hashing(browser):
    # Login now performs a scrypt derivation; the field cap is what stops an unauthenticated
    # caller from making the server burn 64 MB and ~45 ms per request.
    r = browser.post("/auth/login", json={"email": "a@b.co", "password": "x" * 5000})
    assert r.status_code == 422


def test_a_key_can_never_be_named_admin(browser):
    # resolve_session trusts the bare name "admin" as the master principal without a DB check.
    # That is only sound while RESERVED_NAMES holds, so pin it here too.
    from tests.server.conftest import run_on_app_loop

    for name in ["admin", "Admin", "ADMIN", " admin "]:
        with pytest.raises(Exception):  # noqa: B017 — NameTaken or ValueError, both are refusals
            run_on_app_loop(browser, keys.create_key(browser.app.state.db, name))
    assert run_on_app_loop(browser, keys.get_key_by_name(browser.app.state.db, "admin")) is None


def test_tracebacks_never_render_local_variables():
    # A traceback under auth would otherwise print `master` -- the admin credential and the
    # session HMAC key -- in plaintext. auris.log pins this at import for every entry point.
    import structlog

    import auris.log  # noqa: F401 — imported for its configuration side effect

    renderer = structlog.get_config()["processors"][-1]
    formatter = renderer._exception_formatter
    assert getattr(formatter, "show_locals", False) is False
