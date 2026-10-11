from __future__ import annotations

import http.client
import http.server
import json
import threading

import httpx

from auris.client import proxy


class _Upstream(http.server.BaseHTTPRequestHandler):
    """A stand-in auris-server: /auth/login plus the voices endpoints the dashboard calls."""

    seen_cookie = None
    seen_methods: list[str] = []

    def log_message(self, *a):
        pass

    def _login(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = json.dumps({"email": "alice@example.com", "must_change_password": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "auris_session=sess-1; Path=/; HttpOnly")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _voices(self):
        _Upstream.seen_cookie = self.headers.get("Cookie")
        _Upstream.seen_methods.append(self.command)
        body = json.dumps([{"name": "narrator-uk-f", "kind": "preset"}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # No real /v1/* route sets a cookie today, but this stands in for one that someday
        # might — the proxy must never let it reach the browser (see test below).
        self.send_header("Set-Cookie", "evil=should-not-leak; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._voices()

    def do_POST(self):
        if self.path == "/auth/login":
            return self._login()
        return self._voices()

    do_DELETE = do_PATCH = _voices  # the dashboard uses all four verbs against /v1/voices


def _serve(handler) -> tuple[http.server.ThreadingHTTPServer, int]:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_proxy_serves_dashboard_and_injects_session_cookie():
    up, up_port = _serve(_Upstream)
    px, px_port = _serve(
        proxy.handler_for_session(
            f"http://127.0.0.1:{up_port}", "alice@example.com", "a-good-long-password"
        )
    )
    base = f"http://127.0.0.1:{px_port}"
    try:
        assert "<title>auris" in httpx.get(base + "/", timeout=5).text  # serves the dashboard
        assert httpx.get(base + "/__proxy", timeout=5).json()["proxy"] is True  # proxy marker
        r = httpx.get(base + "/v1/voices", timeout=5)  # forwarded to upstream...
        assert r.json()[0]["name"] == "narrator-uk-f"
        # ...with the session cookie injected by the proxy (never a bearer token any more).
        assert _Upstream.seen_cookie == "auris_session=sess-1"

        # Every verb the dashboard uses is forwarded — PATCH (rename) was once missing → 501.
        for method in ("POST", "PATCH", "DELETE"):
            r = httpx.request(method, base + "/v1/voices/x", timeout=5)
            assert r.status_code == 200, (method, r.status_code)
        assert _Upstream.seen_methods[-3:] == ["POST", "PATCH", "DELETE"]

        # DNS-rebinding guard: a foreign Host header is rejected (so the cookie isn't injected).
        r = httpx.get(base + "/v1/voices", headers={"Host": "evil.example.com"}, timeout=5)
        assert r.status_code == 421
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_never_forwards_an_upstream_set_cookie_to_the_browser():
    # _Upstream's /v1/* responses always carry a Set-Cookie (see _voices above). The browser's
    # jar must never see it — only the proxy's own session cookie is allowed to exist, and only
    # inside the proxy process, not in the page.
    up, up_port = _serve(_Upstream)
    px, px_port = _serve(
        proxy.handler_for_session(
            f"http://127.0.0.1:{up_port}", "alice@example.com", "a-good-long-password"
        )
    )
    base = f"http://127.0.0.1:{px_port}"
    try:
        r = httpx.get(base + "/v1/voices", timeout=5)
        assert r.status_code == 200
        assert "set-cookie" not in {k.lower() for k in r.headers.keys()}
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_serves_static_locally_without_upstream():
    # No upstream at all: static files come from the local package, not the service.
    px, px_port = _serve(
        proxy.handler_for_session("http://127.0.0.1:9", "a@example.com", "a-good-long-password")
    )
    try:
        c = http.client.HTTPConnection("127.0.0.1", px_port, timeout=5)
        c.request("GET", "/static/app.css", headers={"Host": f"127.0.0.1:{px_port}"})
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type", "").startswith("text/css")
        assert b"--accent" in r.read()
        # The page is ES modules now, so the js/ subdirectory must come through too.
        c.request("GET", "/static/js/app.js", headers={"Host": f"127.0.0.1:{px_port}"})
        r = c.getresponse()
        assert r.status == 200 and "javascript" in r.getheader("Content-Type", "")
        assert b"detectProxy" in r.read()
        c.request("GET", "/static/../proxy.py", headers={"Host": f"127.0.0.1:{px_port}"})
        assert c.getresponse().status == 404
    finally:
        px.shutdown()
