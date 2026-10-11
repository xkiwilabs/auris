"""Password hashing for auris user accounts.

Pure functions over strings — no database, no FastAPI, nothing heavy. The CLI imports this
module, so it must stay import-light (see docs/architecture.md on the import seam).

`hashlib.scrypt` rather than argon2 or bcrypt: it is in the standard library, so an offline
install needs nothing extra and there is no wheel to vendor for a machine with no network.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

_ALGO = "scrypt"
_N = 2**15  # ~32 MB, ~48 ms on the workstation
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16

# Python's hashlib.scrypt defaults to maxmem=0, which OpenSSL reads as a 32 MB ceiling — and
# n=2**15, r=8 needs 128*n*r = exactly 32 MiB, so the default *fails* with
# "memory limit exceeded". Always pass this explicitly.
_MAXMEM = 64 * 1024 * 1024

MIN_LENGTH = 12
# Deliberately no composition rules: they push people toward predictable substitutions, and a
# password manager is the real answer. Generated initial passwords carry the entropy instead.


def validate_password(password: str) -> str:
    """Return the password unchanged, or raise ValueError (→ 422 at the HTTP edge)."""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    return password


def generate_password() -> str:
    """A high-entropy initial password, printed once by `auris users add`."""
    return secrets.token_urlsafe(18)  # 24 characters, 144 bits


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM
    )


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    """Hash for storage as `scrypt$n$r$p$<b64 salt>$<b64 digest>`.

    The parameters travel with the hash so they can be raised later without invalidating
    everyone's password.
    """
    validate_password(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    return f"{_ALGO}${n}${r}${p}${_b64(salt)}${_b64(_derive(password, salt, n, r, p))}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check against a stored hash. Total: a malformed row returns False."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _ALGO:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return False
    try:
        actual = _derive(password, salt, n, r, p)
    except (
        ValueError,
        TypeError,
    ):  # absurd stored parameters (negative, not a power of two, over maxmem, …)
        return False
    return hmac.compare_digest(actual, expected)
