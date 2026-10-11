"""The shipped remote path: ServiceClient, and the CLI as a subprocess in remote mode.

tests/client/ mocks the HTTP layer, so this is the only coverage the remote profile has against
a real server.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.audio import REFERENCE_TEXT, assert_audible, speech_wav, wer
from tests.e2e.server import SEEDED_VOICE, Instance

pytestmark = pytest.mark.e2e

_ROOT = Path(__file__).resolve().parents[2]


def _cli_env(server: Instance, home: Path) -> dict[str, str]:
    """Remote-mode environment for a CLI subprocess.

    HOME points at a temp dir so the developer's real ~/.config/auris/client.env is neither read
    nor written. The client reads AURIS_BASE_URL + AURIS_AUTH_TOKEN — the same token variable
    name the server uses for its master token.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONPATH": str(_ROOT),
        "PYTHONHASHSEED": "0",
        "AURIS_BASE_URL": server.base_url,
        "AURIS_AUTH_TOKEN": server.token,
    }


def _run(args: list[str], env: dict[str, str], timeout: float = 180.0):
    return subprocess.run(
        [sys.executable, "-m", "auris.cli.main", *args],
        cwd=str(_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_service_client_lists_synthesises_and_transcribes(server: Instance, tmp_path):
    from auris.client.api import ServiceClient

    client = ServiceClient(server.base_url, server.token)
    assert client.ready() is True

    names = {v["name"] for v in client.list_voices()}
    assert SEEDED_VOICE in names and "narrator-uk-f" in names

    out = tmp_path / "client.wav"
    out.write_bytes(client.tts("narrator-uk-f", "Spoken through the remote client.", fmt="wav"))
    assert_audible(out)

    result = client.transcribe(speech_wav(REFERENCE_TEXT).read_bytes())
    assert wer(REFERENCE_TEXT, result["text"]) <= 0.2


def test_service_client_uses_a_named_key_as_readily_as_the_master_token(server: Instance):
    from auris.client.api import ServiceClient

    assert {v["name"] for v in ServiceClient(server.base_url, server.key).list_voices()}


def test_cli_voices_in_remote_mode(server: Instance, tmp_path):
    r = _run(["voices"], _cli_env(server, tmp_path))
    assert r.returncode == 0, r.stderr
    assert SEEDED_VOICE in r.stdout
    assert "narrator-uk-f" in r.stdout


def test_cli_tts_writes_audible_audio(server: Instance, tmp_path):
    out = tmp_path / "cli.wav"
    r = _run(
        ["tts", "narrator-uk-f", "--text", "Spoken by the command line.", "--out", str(out)],
        _cli_env(server, tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert_audible(out)


def test_cli_transcribe_renders_srt(server: Instance, tmp_path):
    wav = speech_wav(REFERENCE_TEXT)
    r = _run(["transcribe", str(wav), "--format", "srt"], _cli_env(server, tmp_path))
    assert r.returncode == 0, r.stderr
    assert "-->" in r.stdout
    assert r.stdout.strip().startswith("1")


def test_cli_keys_refuses_in_remote_mode(server: Instance, tmp_path):
    """Key management is admin-only and server-side; the remote CLI points there instead."""
    r = _run(["keys", "list"], _cli_env(server, tmp_path))
    assert r.returncode != 0
    message = (r.stderr + r.stdout).lower()
    assert "must run on the server box" in message
    assert "/v1/keys" in message  # and it says where to go instead
    assert server.token not in (r.stderr + r.stdout)  # without echoing the caller's token
