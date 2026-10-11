from __future__ import annotations

import pytest

from auris.server import session
from auris.voice_library import keys, users

from .conftest import GOOD_PASSWORD, run_on_app_loop


@pytest.fixture
def browser(as_anon):
    """An anonymous client that keeps whatever cookies the server sets, and sends the CSRF
    header — i.e. the dashboard. httpx persists Set-Cookie into the client's jar, so a
    successful /auth/login leaves the session in place for subsequent calls."""
    c = as_anon()
    c.headers.update({session.CLIENT_HEADER: session.CLIENT_VALUE})
    return c


def _login(browser, email, password):
    return browser.post("/auth/login", json={"email": email, "password": password})


def test_login_with_email_and_password_sets_a_session_cookie(browser, seed_user):
    seed_user("alice@example.com")
    r = _login(browser, "alice@example.com", GOOD_PASSWORD)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["kind"] == "user"
    assert body["admin"] is False
    assert body["must_change_password"] is False
    assert session.COOKIE in browser.cookies
    # The cookie is live: a real route now answers.
    assert browser.get("/v1/voices").status_code == 200


def test_login_is_case_insensitive_on_the_email(browser, seed_user):
    seed_user("alice@example.com")
    assert _login(browser, "ALICE@Example.com", GOOD_PASSWORD).status_code == 200


def test_a_wrong_password_and_an_unknown_email_give_the_identical_401(browser, seed_user):
    seed_user("alice@example.com")
    wrong = _login(browser, "alice@example.com", "wrong-password-here")
    unknown = _login(browser, "nobody@example.com", GOOD_PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    # Identical bodies: revealing that an address has an account is a real disclosure.
    assert wrong.json() == unknown.json()


def test_a_disabled_account_cannot_sign_in(client, browser, seed_user):
    seed_user("alice@example.com")
    run_on_app_loop(client, users.set_disabled(client.app.state.db, "alice@example.com", True))
    assert _login(browser, "alice@example.com", GOOD_PASSWORD).status_code == 401


def test_an_api_key_is_not_a_dashboard_credential(client, browser):
    _, plaintext = run_on_app_loop(client, keys.create_key(client.app.state.db, "ci-bot"))
    # Not a valid email, and not an account either.
    assert _login(browser, "ci-bot", plaintext).status_code in (401, 422)


def test_the_master_token_is_not_a_dashboard_credential(browser):
    assert _login(browser, "admin", "secret").status_code in (401, 422)


def test_login_reports_a_pending_password_change(browser, seed_user):
    seed_user("new@example.com", must_change_password=True)
    r = _login(browser, "new@example.com", GOOD_PASSWORD)
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True


def test_changing_the_password_clears_the_flag_and_issues_a_fresh_cookie(browser, seed_user):
    seed_user("new@example.com", must_change_password=True)
    _login(browser, "new@example.com", GOOD_PASSWORD)
    old_cookie = browser.cookies[session.COOKIE]

    r = browser.post(
        "/auth/password",
        json={"current_password": GOOD_PASSWORD, "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 200
    assert r.json()["must_change_password"] is False
    new_cookie = browser.cookies[session.COOKIE]  # the response replaced it in the jar
    assert new_cookie != old_cookie

    # The new cookie works on a real route (the gate is cleared); the old one does not.
    assert browser.get("/v1/voices").status_code == 200
    browser.cookies.clear()
    browser.cookies.set(session.COOKIE, old_cookie)
    assert browser.get("/v1/voices").status_code == 401


def test_changing_the_password_requires_the_current_one(browser, seed_user):
    """403 with its own code, NOT 401 — and that distinction is load-bearing, not cosmetic.

    `static/js/api.js` routes every 401 to the "your session is gone" handler, which drops the user
    back on the sign-in screen. While this returned 401, mistyping your own current password logged
    you out in the middle of changing it (caught in the browser, not by this suite). The session is
    valid here; only the re-authentication check on the body failed.
    """
    seed_user("alice@example.com")
    _login(browser, "alice@example.com", GOOD_PASSWORD)
    r = browser.post(
        "/auth/password",
        json={"current_password": "not-the-password", "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "current_password_invalid"


def test_a_refused_current_password_leaves_the_session_usable(browser, seed_user):
    """The pair to the test above: proves the session really did survive the refusal.

    Asserting the status alone would still pass if the route started clearing the cookie, which is
    the other way a user could end up signed out by a typo.
    """
    seed_user("alice@example.com")
    _login(browser, "alice@example.com", GOOD_PASSWORD)
    browser.post(
        "/auth/password",
        json={"current_password": "not-the-password", "new_password": "a-brand-new-password"},
    )
    assert browser.get("/auth/me").status_code == 200


def test_a_short_new_password_is_refused(browser, seed_user):
    seed_user("alice@example.com")
    _login(browser, "alice@example.com", GOOD_PASSWORD)
    r = browser.post(
        "/auth/password", json={"current_password": GOOD_PASSWORD, "new_password": "short"}
    )
    assert r.status_code == 422


def test_the_master_token_cannot_change_a_password(client):
    """It has no account, so there is nothing to change."""
    r = client.post(
        "/auth/password",
        json={"current_password": "secret", "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 403


def test_me_reports_the_account(browser, seed_user):
    seed_user("boss@example.com", role="admin")
    _login(browser, "boss@example.com", GOOD_PASSWORD)
    body = browser.get("/auth/me").json()
    assert body["email"] == "boss@example.com"
    assert body["role"] == "admin"
    assert body["admin"] is True
    assert body["kind"] == "user"
    assert body["expires"] > 0


def test_a_user_cannot_change_another_accounts_password(browser, seed_user):
    """The target is always the session's own account.

    `PasswordChangeRequest` has no target field and pydantic ignores extra keys by default, so a
    smuggled `user_id`/`email` must be inert rather than redirecting the change. Pinned because
    that safety currently rests on the model's *shape* — add a field named like these and the
    protection would vanish with nothing else failing.
    """
    seed_user("alice@example.com")
    victim = seed_user("victim@example.com")
    _login(browser, "alice@example.com", GOOD_PASSWORD)

    r = browser.post(
        "/auth/password",
        json={
            "current_password": GOOD_PASSWORD,
            "new_password": "alices-brand-new-password",
            # Every shape a caller might hope redirects the change at someone else.
            "user_id": victim.id,
            "email": "victim@example.com",
            "target": "victim@example.com",
        },
    )
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"  # the caller, not the victim

    # The victim's password is untouched; Alice's really did change.
    assert _login(browser, "victim@example.com", GOOD_PASSWORD).status_code == 200
    assert _login(browser, "alice@example.com", GOOD_PASSWORD).status_code == 401
    assert _login(browser, "alice@example.com", "alices-brand-new-password").status_code == 200
