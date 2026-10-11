from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "token.sh"


def _run(args, env_file, **kw):
    env = {**os.environ, "AURIS_ENV_FILE": str(env_file)}
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True, **kw
    )


def test_set_then_show(tmp_path):
    env_file = tmp_path / ".env"
    setr = _run(["set"], env_file, input="abc123\n")  # token via stdin, not the command line
    assert setr.returncode == 0
    assert "abc123" not in setr.stdout  # `set` must not echo the token

    show = _run(["show"], env_file)
    assert show.returncode == 0 and show.stdout.strip() == "abc123"

    # set replaces (not appends duplicates)
    _run(["set"], env_file, input="def456\n")
    assert _run(["show"], env_file).stdout.strip() == "def456"
    assert env_file.read_text().count("AURIS_AUTH_TOKEN=") == 1


def test_show_missing_errors(tmp_path):
    r = _run(["show"], tmp_path / ".env")
    assert r.returncode == 1
