from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

# Where the remote-client settings (service URL + token) are stored. Only this user-owned file and
# environment variables are trusted — we deliberately do NOT read a client.env from the current
# working directory, so a stray/hostile file in a cloned repo can't redirect the token.
USER_CONFIG = Path.home() / ".config" / "auris" / "client.env"


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def load_client_config() -> dict[str, str]:
    """Resolve {base_url, token}. Priority: environment variables > user config file."""
    merged = _parse(USER_CONFIG)
    for key in ("AURIS_BASE_URL", "AURIS_AUTH_TOKEN"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return {
        "base_url": merged.get("AURIS_BASE_URL", "").rstrip("/"),
        "token": merged.get("AURIS_AUTH_TOKEN", ""),
    }


def save_client_config(base_url: str | None = None, token: str | None = None) -> Path:
    """Update the user config file with the given values (leave unset ones untouched).

    The token is a secret, so the file is created atomically at mode 0600 — the bytes never touch
    disk under looser permissions (no TOCTOU window)."""
    cur = _parse(USER_CONFIG)
    if base_url is not None:
        cur["AURIS_BASE_URL"] = base_url.rstrip("/")
    if token is not None:
        cur["AURIS_AUTH_TOKEN"] = token
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        USER_CONFIG.parent.chmod(0o700)
    except OSError:
        pass
    _atomic_write(USER_CONFIG, "".join(f"{k}={v}\n" for k, v in cur.items()))
    return USER_CONFIG


def _atomic_write(path: Path, text: str) -> None:
    # mkstemp creates the file 0600 by default, so the secret is never world/group-readable.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".client.env.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def mask(token: str) -> str:
    """A safe-to-display rendering of a token (never the full value)."""
    if not token:
        return "(none)"
    return ("…" + token[-4:]) if len(token) > 4 else "…"
