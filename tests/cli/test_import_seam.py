"""The CLI must import with only the light dependencies installed.

`auris/cli/commands/transcribe.py` imports `auris.server.transcript_format` — the first
and only import of `auris.server.*` from outside the server package — and `cli/main.py`
makes it unconditional for every `auris` invocation. `fastapi` lives in the optional
`server` extra, but it *is* installed in the dev venv, so a stray `from fastapi import ...`
in a server module the CLI reaches would pass the whole suite and then break every
`.[client]`-only install at startup. This test is the guard.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The server extra plus every heavy runtime the lazy-import rule covers.
BLOCKED = (
    "torch",
    "f5_tts",
    "faster_whisper",
    "ctranslate2",
    "sounddevice",
    "kokoro",
    "fastapi",
    "starlette",
    "uvicorn",
)

_SCRIPT = f"""
import builtins, sys

blocked = {BLOCKED!r}
_real_import = builtins.__import__


def _guard(name, *args, **kwargs):
    root = name.split(".")[0]
    if root in blocked:
        raise ImportError(
            "auris.cli.main imported " + name + ", which is not in the client profile"
        )
    return _real_import(name, *args, **kwargs)


builtins.__import__ = _guard
import auris.cli.main as m
builtins.__import__ = _real_import
print(m.__file__)
"""


def test_cli_main_imports_without_fastapi_or_any_model_runtime():
    # A subprocess, not this interpreter: the suite has already imported auris.cli.main
    # (and fastapi), so an in-process import would be a no-op and prove nothing.
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=REPO_ROOT,  # so the checkout under test wins over any installed copy
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # Guard against the subprocess having imported a *different* auris.
    assert proc.stdout.strip().startswith(str(REPO_ROOT))
