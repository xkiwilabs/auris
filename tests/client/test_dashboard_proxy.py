from __future__ import annotations

import http.client
import http.server
import json
import threading

import httpx
import pytest

from auris.client import proxy


class _Upstream(http.server.BaseHTTPRequestHandler):
    """A stand-in auris-server: /auth/login and /auth/logout set and clear the session cookie the
    way the real one does; everything else records what arrived and answers 200 (or 401 with no
    session, like the real /v1/*)."""

    seen: list[tuple[str, str, str | None]] = []  # (method, path, Cookie header)

    def log_message(self, *a):
        pass

    def _reply(self, status, payload, set_cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        cookie = self.headers.get("Cookie")
        _Upstream.seen.append((self.command, self.path, cookie))
        if self.path == "/auth/login":
            # What a TLS deployment would send: Secure (and a Domain, which some proxies add).
            return self._reply(
                200,
                {"email": "alice@example.com"},
                "auris_session=sess-1; Domain=auris.example; HttpOnly; Max-Age=43200; Path=/; "
                "SameSite=strict; Secure",
            )
        if self.path == "/auth/logout":
            return self._reply(200, {"ok": True}, 'auris_session=""; Max-Age=0; Path=/')
        if self.path.startswith("/v1/") and cookie != "auris_session=sess-1":
            return self._reply(401, {"error": {"code": "unauthorized"}})
        # No real /v1/* route sets a cookie, but one that someday might must not reach the page.
        return self._reply(200, [{"name": "narrator-uk-f"}], "evil=should-not-leak; Path=/")

    do_GET = do_POST = do_DELETE = do_PATCH = _handle


def _serve(handler) -> tuple[http.server.ThreadingHTTPServer, int]:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.fixture
def pair():
    """(proxy base URL, the browser's session cookie name) in front of a fresh _Upstream."""
    _Upstream.seen = []
    up, up_port = _serve(_Upstream)
    upstream = f"http://127.0.0.1:{up_port}"
    px, px_port = _serve(proxy.handler_for(upstream))
    yield f"http://127.0.0.1:{px_port}", proxy.browser_cookie_name(px_port, upstream)
    px.shutdown()
    up.shutdown()


def test_the_proxy_holds_no_credential_of_its_own(pair):
    base, _ = pair
    r = httpx.get(base + "/v1/voices", timeout=5)
    # Nothing injected: the server sees no cookie and its own 401 comes straight back to the page,
    # which then shows its sign-in screen.
    assert r.status_code == 401
    assert _Upstream.seen[-1] == ("GET", "/v1/voices", None)


def test_signing_in_through_the_page_hands_the_browser_its_own_cookie(pair):
    base, cname = pair
    r = httpx.post(base + "/auth/login", json={"email": "a", "password": "b"}, timeout=5)
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    (cookie,) = r.headers.get_list("set-cookie")
    name, _, rest = cookie.partition("=")
    assert name == cname
    assert rest.startswith("sess-1;")
    attrs = {a.strip().split("=")[0].lower() for a in rest.split(";")[1:]}
    assert {"httponly", "path", "samesite", "max-age"} <= attrs
    # Secure would stop the cookie being sent over http://127.0.0.1 in some browsers, and a Domain
    # names the server's host, not this one — both are dropped.
    assert "secure" not in attrs and "domain" not in attrs


def test_the_browsers_cookie_is_forwarded_as_the_servers_and_nothing_else_is(pair):
    base, cname = pair
    cookies = {cname: "sess-1", "other_localhost_app": "private"}
    r = httpx.get(base + "/v1/voices", cookies=cookies, timeout=5)
    assert r.status_code == 200 and r.json()[0]["name"] == "narrator-uk-f"
    # Only the session goes to the remote server, under the name it set — never the browser's
    # other 127.0.0.1 cookies.
    assert _Upstream.seen[-1][2] == "auris_session=sess-1"


def test_the_cookie_name_is_per_port_and_per_server():
    # Cookies are scoped to a host, not a port, and outlive the proxy (12 h). Naming by port alone
    # would hand a session for one server to whatever server the next proxy on that port fronts.
    a = proxy.browser_cookie_name(8765, "http://server-a:8011")
    assert a.startswith("auris_session_8765_")
    assert a != proxy.browser_cookie_name(8765, "http://server-b:8011")
    assert a != proxy.browser_cookie_name(8766, "http://server-a:8011")
    assert a == proxy.browser_cookie_name(8765, "http://server-a:8011/")  # trailing slash


def test_a_session_for_another_server_is_never_forwarded(pair):
    base, cname = pair
    port = int(base.rsplit(":", 1)[1])
    stale = proxy.browser_cookie_name(port, "http://some-other-server:8011")
    r = httpx.get(base + "/v1/voices", cookies={stale: "sess-1"}, timeout=5)
    assert r.status_code == 401
    assert _Upstream.seen[-1][2] is None


def test_signing_out_clears_the_browsers_cookie(pair):
    base, cname = pair
    r = httpx.post(base + "/auth/logout", cookies={cname: "sess-1"}, timeout=5)
    (cookie,) = r.headers.get_list("set-cookie")
    assert cookie.startswith(f'{cname}="";') and "Max-Age=0" in cookie


def test_an_upstream_cookie_other_than_the_session_never_reaches_the_browser(pair):
    base, cname = pair
    r = httpx.get(base + "/v1/voices", cookies={cname: "sess-1"}, timeout=5)
    assert r.status_code == 200
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}


def test_every_route_the_page_uses_is_forwarded(pair):
    base, cname = pair
    jar = {cname: "sess-1"}
    for method, path in [
        ("GET", "/auth/me"),
        ("POST", "/auth/password"),
        ("GET", "/ready"),
        ("POST", "/v1/voices/x"),
        ("PATCH", "/v1/voices/x"),  # rename — once missing, which answered 501
        ("DELETE", "/v1/voices/x"),
    ]:
        r = httpx.request(method, base + path, cookies=jar, timeout=5)
        assert r.status_code == 200, (method, path, r.status_code)
        assert _Upstream.seen[-1][:2] == (method, path)


def test_a_foreign_host_header_is_refused(pair):
    # DNS-rebinding guard: a page that rebinds its name to 127.0.0.1 still sends its own Host, so
    # it can't use the proxy as a relay onto the server's network.
    base, _ = pair
    r = httpx.get(base + "/v1/voices", headers={"Host": "evil.example.com"}, timeout=5)
    assert r.status_code == 421
    r = httpx.post(base + "/auth/login", headers={"Origin": "http://evil.example.com"}, timeout=5)
    assert r.status_code == 421


def test_the_proxy_serves_the_dashboard_and_static_files_locally():
    # No upstream at all: the page and its assets come from the local package, not the service.
    px, px_port = _serve(proxy.handler_for("http://127.0.0.1:9"))
    try:
        c = http.client.HTTPConnection("127.0.0.1", px_port, timeout=5)
        host = {"Host": f"127.0.0.1:{px_port}"}
        c.request("GET", "/", headers=host)
        assert b"<title>auris" in c.getresponse().read()
        c.request("GET", "/static/app.css", headers=host)
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type", "").startswith("text/css")
        assert b"--a1" in r.read()
        c.request("GET", "/static/js/app.js", headers=host)
        r = c.getresponse()
        assert r.status == 200 and "javascript" in r.getheader("Content-Type", "")
        r.read()
        c.request("GET", "/static/../proxy.py", headers=host)
        assert c.getresponse().status == 404
    finally:
        px.shutdown()


def test_an_unreachable_server_is_a_502_naming_it():
    px, px_port = _serve(proxy.handler_for("http://127.0.0.1:9"))
    try:
        r = httpx.get(f"http://127.0.0.1:{px_port}/v1/voices", timeout=5)
        assert r.status_code == 502
        assert "127.0.0.1:9" in r.json()["error"]["message"]
    finally:
        px.shutdown()


def test_starting_against_an_unreachable_server_fails_loudly():
    with pytest.raises(RuntimeError, match="could not reach http://127.0.0.1:9"):
        proxy.serve_dashboard("http://127.0.0.1:9", port=0, open_browser=False)
