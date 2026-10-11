"""List voices, then synthesise one line to out.mp3. Needs only `requests`.

export AURIS_BASE_URL=http://<host>:<port>      # e.g. http://your-server:8011
export AURIS_AUTH_TOKEN=<token>                 # grep AURIS_AUTH_TOKEN .env on the server
python examples/python_quickstart.py "Hello from auris." MyVoice
"""

from __future__ import annotations

import os
import sys

import requests

BASE = os.environ.get("AURIS_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
TOKEN = os.environ["AURIS_AUTH_TOKEN"]
HEAD = {"Authorization": f"Bearer {TOKEN}"}


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello from auris."
    voices = requests.get(f"{BASE}/v1/voices", headers=HEAD, timeout=10).json()
    if not voices:
        sys.exit("No voices yet — clone one first (see clone_and_speak.py).")
    voice = sys.argv[2] if len(sys.argv) > 2 else voices[0]["name"]
    print(f"voices: {[v['name'] for v in voices]}  ->  using {voice!r}")

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
