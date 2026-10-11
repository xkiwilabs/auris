"""Clone a voice from a wav, then synthesise with it. Needs only `requests`.

    export AURIS_BASE_URL=http://<host>:<port>
    export AURIS_AUTH_TOKEN=<token>
    python examples/clone_and_speak.py MyVoice ref.wav "Now I sound like the reference."

The reference should be ~3-15s of clear speech (wav/mp3/m4a/… all accepted). If you omit the
transcript it is auto-transcribed server-side.
"""

from __future__ import annotations

import os
import sys

import requests

BASE = os.environ.get("AURIS_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
TOKEN = os.environ["AURIS_AUTH_TOKEN"]
HEAD = {"Authorization": f"Bearer {TOKEN}"}


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: clone_and_speak.py <voice> <ref.wav> [text]")
    voice, ref = sys.argv[1], sys.argv[2]
    text = sys.argv[3] if len(sys.argv) > 3 else "This is my cloned voice."

    with open(ref, "rb") as f:
        r = requests.post(
            f"{BASE}/v1/voices/{voice}/clone",
            headers=HEAD,
            files={"file": (os.path.basename(ref), f, "application/octet-stream")},
            timeout=180,
        )
    r.raise_for_status()
    print("cloned:", r.json())

    r = requests.post(
        f"{BASE}/v1/audio/speech",
        headers=HEAD,
        json={"input": text, "voice": voice, "response_format": "mp3"},
        timeout=120,
    )
    r.raise_for_status()
    with open("out.mp3", "wb") as f:
        f.write(r.content)
    print(f"wrote out.mp3 ({len(r.content)} bytes)")


if __name__ == "__main__":
    main()
