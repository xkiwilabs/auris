"""Use the OpenAI SDK against auris — a drop-in for agents / research code. Needs `openai`.

    pip install openai
    export AURIS_BASE_URL=http://<host>:<port>
    export AURIS_AUTH_TOKEN=<token>
    python examples/openai_sdk.py MyVoice "Generated through the OpenAI client."

auris implements the OpenAI /v1/audio/speech contract, so any OpenAI-compatible client works by
pointing base_url at the service and using the auth token as the api_key.
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

BASE = os.environ.get("AURIS_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
client = OpenAI(base_url=f"{BASE}/v1", api_key=os.environ["AURIS_AUTH_TOKEN"])


def main() -> None:
    voice = sys.argv[1] if len(sys.argv) > 1 else "MyVoice"
    text = sys.argv[2] if len(sys.argv) > 2 else "Generated through the OpenAI client."
    resp = client.audio.speech.create(
        model="f5_tts", voice=voice, input=text, response_format="mp3"
    )
    resp.stream_to_file("out.mp3")
    print("wrote out.mp3")


if __name__ == "__main__":
    main()
