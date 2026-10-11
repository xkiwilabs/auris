from __future__ import annotations

import hashlib
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
_PROXIED_PREFIXES = ("/ready", "/health", "/auth/", "/v1/")
_HOP_BY_HOP = {"connection", "transfer-encoding", "content-length", "keep-alive", "host"}

SESSION_COOKIE = "auris_session"  # literal, not imported: auris/client/ must not import server code
_DROPPED_ATTRS = {"secure", "domain"}


def browser_cookie_name(port: int, base_url: str) -> str:
    """The name the session cookie goes by in the browser.

    Cookies are scoped to a host, not a port, and this one outlives the proxy (it keeps the
    server's 12-hour lifetime). Named by port alone, a session for one server would be sent to
    whatever server the next proxy on that port fronts — so the server is in the name too."""
    tag = hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()[:8]
    return f"{SESSION_COOKIE}_{port}_{tag}"


def session_from_cookie_header(header: str, name: str) -> str | None:
    """This proxy's session from the browser's Cookie header, if it holds one."""
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def rewrite_set_cookie(header: str, name: str) -> str | None:
    """The server's session Set-Cookie, renamed for the browser; None for any other cookie.

    `Secure` is dropped because the page is plain http://127.0.0.1 (a secure context, but not
    every browser will send a Secure cookie to it), and `Domain` because it names the server's
    host, not this one. HttpOnly, SameSite, Path and the lifetime are kept as the server set them.
    """
    first, _, rest = header.partition(";")
    key, _, value = first.strip().partition("=")
    if key != SESSION_COOKIE:
        return None
    attrs = [a.strip() for a in rest.split(";") if a.strip()]
    kept = [a for a in attrs if a.partition("=")[0].strip().lower() not in _DROPPED_ATTRS]
    return "; ".join([f"{name}={value}", *kept])


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Serve the bundled dashboard at / and /static/*, and forward /ready, /health, /auth/* and
    /v1/* to the configured remote service. The proxy holds no credential: the page signs in
    exactly as it does on the server itself, and the proxy only renames the session cookie on
    the way through (see browser_cookie_name). Subclasses set base_url (handler_for)."""

    base_url = ""

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
        its own Host header, so this stops it using the proxy as a relay onto the server's
        network (to reach it at all, the page has to be the one this proxy serves)."""
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
        name = browser_cookie_name(self.server.server_address[1], self.base_url)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        # Only this proxy's session goes to the server, under the name the server gave it — never
        # the browser's other 127.0.0.1 cookies.
        headers.pop("Cookie", None)
        sess = session_from_cookie_header(self.headers.get("Cookie", ""), name)
        if sess:
            headers["Cookie"] = f"{SESSION_COOKIE}={sess}"
        upstream = cls(u.netloc, timeout=300)
        try:
            try:
                upstream.request(method, self.path, body=body, headers=headers)
                resp = upstream.getresponse()
            except OSError as exc:
                message = f"could not reach {self.base_url}: {exc}"
                self._send_bytes(
                    502,
                    json.dumps({"error": {"code": "upstream", "message": message}}).encode(),
                    "application/json",
                )
                return
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in _HOP_BY_HOP:
                    continue
                if k.lower() == "set-cookie":
                    v = rewrite_set_cookie(v, name)  # any other upstream cookie is dropped
                    if v is None:
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
        finally:
            upstream.close()

    def do_GET(self):
        if not self._local_only():
            self.send_error(421)
            return
        path = self._path()
        if path == "/":
            self._send_bytes(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
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


def handler_for(base_url: str) -> type[_ProxyHandler]:
    """A handler subclass bound to one service URL."""
    return type("_BoundProxyHandler", (_ProxyHandler,), {"base_url": base_url.rstrip("/")})


def _check_reachable(base_url: str) -> None:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=10):
            pass
    except urllib.error.HTTPError:
        pass  # it answered: reachable
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"could not reach {base_url}: {reason}") from exc


def serve_dashboard(base_url: str, port: int = 8765, open_browser: bool = True) -> None:
    """Serve the dashboard locally and proxy to the remote service. Blocks until Ctrl-C."""
    base_url = base_url.rstrip("/")
    _check_reachable(base_url)  # fail loudly now, not on the first page load
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_for(base_url))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True
        ).start()
    print(f"auris dashboard → {url}  (proxying {base_url}) — sign in there; Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
