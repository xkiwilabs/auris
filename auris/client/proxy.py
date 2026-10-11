from __future__ import annotations

import http.client
import http.server
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

_INDEX = Path(__file__).resolve().parent.parent / "server" / "static" / "index.html"
_STATIC_DIR = _INDEX.parent  # served locally so page + assets always come from one bundle
_PROXIED_PREFIXES = ("/ready", "/health", "/v1/")
_HOP_BY_HOP = {"connection", "transfer-encoding", "content-length", "keep-alive", "host"}

SESSION_COOKIE = "auris_session"  # literal, not imported: auris/client/ must not import server code
CLIENT_HEADER = "X-Auris-Client"
CLIENT_VALUE = "dashboard"


class _Session:
    """Holds a dashboard session on the browser's behalf.

    The password lives in memory for the life of this process and is never written to disk — the
    point of the proxy is that no credential reaches the page, and persisting one would trade
    that for a worse problem. A 12-hour session will expire while the proxy is still running, so
    a 401 re-logs in rather than making the person restart it.
    """

    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self._password = password
        self._cookie: str | None = None

    def ensure_session(self) -> str:
        if self._cookie:
            return self._cookie
        body = json.dumps({"email": self.email, "password": self._password}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/auth/login",
            data=body,
            headers={"Content-Type": "application/json", CLIENT_HEADER: CLIENT_VALUE},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read() or b"{}")
                cookie = self._cookie_from(resp.headers.get_all("Set-Cookie") or [])
        except urllib.error.HTTPError as exc:
            detail = "email or password not accepted" if exc.code == 401 else f"HTTP {exc.code}"
            raise RuntimeError(f"could not sign in to {self.base_url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"could not reach {self.base_url}: {exc.reason}") from exc
        if payload.get("must_change_password"):
            raise RuntimeError(
                "this account must set a new password before it can be used. Sign in to the "
                f"dashboard at {self.base_url} directly, change it, then run this again."
            )
        if not cookie:
            raise RuntimeError(f"{self.base_url} accepted the sign-in but set no session cookie")
        self._cookie = cookie
        return cookie

    @staticmethod
    def _cookie_from(set_cookie_headers: list[str]) -> str | None:
        for header in set_cookie_headers:
            first = header.split(";", 1)[0].strip()
            name, _, value = first.partition("=")
            if name == SESSION_COOKIE and value:
                return value
        return None

    def auth_headers(self) -> dict[str, str]:
        """What to attach to a forwarded request. Never an Authorization header: the master token
        is no longer a dashboard credential and an API key can no longer open a session."""
        return {
            "Cookie": f"{SESSION_COOKIE}={self.ensure_session()}",
            CLIENT_HEADER: CLIENT_VALUE,
        }

    def invalidate(self) -> None:
        self._cookie = None


def forward_with_retry(session, send):
    """Run `send(headers) -> (status, body)` upstream, re-signing in once on a 401.

    Exactly one retry: a 12-hour cookie expiring mid-session should be invisible, but a
    credential that is genuinely rejected must not loop.
    """
    for attempt in (1, 2):
        status, body = send(session.auth_headers())
        if status != 401 or attempt == 2:
            return status, body
        session.invalidate()  # the cookie expired, or the master token was rotated
    raise AssertionError("unreachable")


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Serve the bundled dashboard at /, mark itself via /__proxy, and forward /ready + /v1/* to
    the configured remote service with the session cookie injected. Subclasses set base_url +
    session (handler_for_session)."""

    base_url = ""
    session: _Session | None = None

    def log_message(self, *args):  # quiet
        pass

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_static(self, path: str) -> None:
        """GET /static/<file> from the bundled package dir, refusing anything that escapes it."""
        rel = path[len("/static/") :]
        target = (_STATIC_DIR / rel).resolve()
        if not target.is_relative_to(_STATIC_DIR.resolve()) or not target.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), ctype)

    def _path(self) -> str:
        return self.path.split("?", 1)[0]

    def _local_only(self) -> bool:
        """Guard against DNS rebinding: only serve requests whose Host (and Origin, if present)
        is this loopback server. A malicious page that rebinds its name to 127.0.0.1 still sends
        its own Host header, so this blocks it from abusing the token-injecting proxy."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").lower() not in hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in hosts}:
            return False
        return True

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        u = urllib.parse.urlsplit(self.base_url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection

        def send(auth_headers: dict[str, str]) -> tuple[int, bytes]:
            upstream = cls(u.netloc, timeout=300)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
            # Strip whatever the browser sent *before* the injected credential is applied, so the
            # page can never influence which session the proxy uses.
            headers.pop("Authorization", None)
            headers.pop("Cookie", None)
            headers.update(auth_headers)
            try:
                upstream.request(method, self.path, body=body, headers=headers)
                resp = upstream.getresponse()
                if resp.status == 401:
                    resp.read()  # drain so the connection closes cleanly; nothing sent yet
                    return 401, b""
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in _HOP_BY_HOP:
                        continue
                    if k.lower() == "set-cookie":
                        # Never forward an upstream Set-Cookie into the browser's jar: the only
                        # cookie this page may hold is the one the proxy itself injects per
                        # request, server-side, from `self.session`. No current /v1/* route sets
                        # one (only /auth/login and /auth/password do, and neither is forwarded —
                        # see _PROXIED_PREFIXES), but without this guard a future one that did
                        # would leak the proxy's own session cookie straight into the page.
                        continue
                    self.send_header(k, v)
                self.send_header("Connection", "close")  # body ends at EOF → streaming passthrough
                self.end_headers()
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return resp.status, b""
            finally:
                upstream.close()

        try:
            status, _ = forward_with_retry(self.session, send)
        except (RuntimeError, OSError) as exc:
            self._send_bytes(
                502,
                json.dumps({"error": {"code": "upstream", "message": str(exc)}}).encode(),
                "application/json",
            )
            return
        if status == 401:
            # Both attempts came back 401 — a genuinely rejected re-login, not an expired cookie.
            # Nothing has been written to the browser yet, so this is the one response for it.
            self._send_bytes(
                401,
                json.dumps(
                    {"error": {"code": "unauthorized", "message": "session invalid"}}
                ).encode(),
                "application/json",
            )

    def do_GET(self):
        if not self._local_only():
            self.send_error(421)
            return
        path = self._path()
        if path == "/":
            self._send_bytes(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/__proxy":
            self._send_bytes(200, json.dumps({"proxy": True}).encode(), "application/json")
        elif path.startswith("/static/"):
            self._local_static(path)
        elif path.startswith(_PROXIED_PREFIXES):
            self._forward("GET")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._local_only():
            self.send_error(421)
        elif self._path().startswith(_PROXIED_PREFIXES):
            self._forward("POST")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._local_only():
            self.send_error(421)
        elif self._path().startswith(_PROXIED_PREFIXES):
            self._forward("DELETE")
        else:
            self.send_error(404)

    def do_PATCH(self):  # voice rename
        if not self._local_only():
            self.send_error(421)
        elif self._path().startswith(_PROXIED_PREFIXES):
            self._forward("PATCH")
        else:
            self.send_error(404)


def handler_for_session(base_url: str, email: str, password: str) -> type[_ProxyHandler]:
    """A handler subclass bound to one service URL and one signed-in account."""
    return type(
        "_BoundProxyHandler",
        (_ProxyHandler,),
        {"base_url": base_url.rstrip("/"), "session": _Session(base_url, email, password)},
    )


def serve_dashboard(
    base_url: str, email: str, password: str, port: int = 8765, open_browser: bool = True
) -> None:
    """Serve the dashboard locally and proxy to the remote service. Blocks until Ctrl-C."""
    handler = handler_for_session(base_url, email, password)
    handler.session.ensure_session()  # fail loudly now, not on the first page load
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True
        ).start()
    print(f"auris dashboard → {url}  (proxying {base_url.rstrip('/')})  — Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
