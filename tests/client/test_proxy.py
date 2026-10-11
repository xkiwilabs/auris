from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from auris.client import proxy

COOKIE = "auris_session"


class _FakeUpstream(BaseHTTPRequestHandler):
    """A stand-in auris-server that only implements /auth/login."""

    logins: list[dict] = []
    session_value = "session-1"
    status = 200
    must_change = False

    def log_message(self, *a):  # keep pytest output clean
        pass

    def do_POST(self):
        if self.path != "/auth/login":
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).logins.append({**body, "client": self.headers.get("X-Auris-Client")})
        if type(self).status != 200:
            return self.send_error(type(self).status, "email or password not accepted")
        payload = json.dumps(
            {"email": body["email"], "must_change_password": type(self).must_change}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", f"{COOKIE}={type(self).session_value}; Path=/; HttpOnly")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def upstream():
    _FakeUpstream.logins = []
    _FakeUpstream.session_value, _FakeUpstream.status = "session-1", 200
    _FakeUpstream.must_change = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _session(upstream, password="a-good-long-password"):
    return proxy._Session(upstream, "alice@example.com", password)


def test_the_session_logs_in_with_email_and_password(upstream):
    _session(upstream).ensure_session()
    assert _FakeUpstream.logins == [
        {
            "email": "alice@example.com",
            "password": "a-good-long-password",
            "client": "dashboard",
        }
    ]


def test_the_cookie_is_read_from_set_cookie_and_reused(upstream):
    s = _session(upstream)
    assert s.ensure_session() == "session-1"
    assert s.ensure_session() == "session-1"
    assert len(_FakeUpstream.logins) == 1  # cached, not re-fetched per request


def test_auth_headers_carry_the_cookie_and_the_csrf_header_but_never_a_bearer(upstream):
    headers = _session(upstream).auth_headers()
    assert headers["Cookie"] == f"{COOKIE}=session-1"
    assert headers["X-Auris-Client"] == "dashboard"
    assert "Authorization" not in headers


def test_a_401_triggers_exactly_one_re_login_and_one_retry(upstream):
    """A 12-hour session expires while the proxy is still running; the person should not have to
    restart it. But a genuinely bad credential must not loop."""
    s = _session(upstream)
    s.ensure_session()
    _FakeUpstream.session_value = "session-2"
    sent = []

    def send(headers):
        sent.append(headers["Cookie"])
        return (401, b"") if len(sent) == 1 else (200, b'{"ok":true}')

    status, body = proxy.forward_with_retry(s, send)
    assert (status, body) == (200, b'{"ok":true}')
    assert sent == [f"{COOKIE}=session-1", f"{COOKIE}=session-2"]
    assert len(_FakeUpstream.logins) == 2


def test_a_persistent_401_is_returned_rather_than_retried_forever(upstream):
    s = _session(upstream)
    calls = []

    def send(headers):
        calls.append(headers)
        return 401, b"nope"

    status, _body = proxy.forward_with_retry(s, send)
    assert status == 401
    assert len(calls) == 2  # one retry, then give up


def test_a_rejected_password_raises_a_clear_error(upstream):
    _FakeUpstream.status = 401
    with pytest.raises(RuntimeError, match="not accepted"):
        _session(upstream, "wrong-password-here").ensure_session()


def test_an_account_pending_a_password_change_is_told_where_to_change_it(upstream):
    _FakeUpstream.must_change = True
    with pytest.raises(RuntimeError, match="new password"):
        _session(upstream).ensure_session()


def test_an_unreachable_upstream_raises_a_clear_error():
    with pytest.raises(RuntimeError, match="could not reach"):
        proxy._Session(
            "http://127.0.0.1:1", "a@example.com", "a-good-long-password"
        ).ensure_session()


def test_the_password_is_never_written_to_disk(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    _session(upstream).ensure_session()
    written = [p.read_text(errors="replace") for p in tmp_path.rglob("*") if p.is_file()]
    assert not any("a-good-long-password" in text for text in written)
