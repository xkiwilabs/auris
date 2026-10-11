# auris examples

Copy-paste starting points for using a **running** auris service from your own code — no need to
install auris itself. You only need `requests` (or `openai` for the SDK example).

Easiest setup — the client script makes a `.venv-client` with both deps:

```bash
./scripts/setup-client.sh             # macOS/Linux
.\scripts\setup-client.ps1            # Windows (PowerShell)
```

Or install them yourself:

```bash
pip install requests          # for the python_quickstart / clone_and_speak examples
pip install openai            # only for openai_sdk.py
```

> New to the service? The **[remote user guide](../docs/remote-users.md)** walks through the
> dashboard and these examples from scratch.

Point them at the service with two environment variables:

```bash
export AURIS_BASE_URL=http://<host>:<port>     # e.g. http://your-server:8011
export AURIS_AUTH_TOKEN=<token>                # on the server: grep AURIS_AUTH_TOKEN .env
```

| Example | What it does |
|---------|--------------|
| `python_quickstart.py` | List voices, then synthesise a line to `out.mp3`. |
| `clone_and_speak.py`   | Clone a voice from a reference clip, then synthesise with it. |
| `openai_sdk.py`        | Same synthesis via the OpenAI SDK (drop-in for agents / research code). |
| `curl.sh`              | The same calls as plain `curl` one-liners. |

Prefer a browser? Just open `http://<host>:<port>/` — the dashboard does voices, cloning
(upload or mic), and type-and-speak with no code at all.

Audio is returned as **mp3** by default (compact over the network); request `wav`, `opus`, or
`pcm` via `response_format` / `format` if you need them. For low-latency playback, stream raw PCM
from `POST /v1/tts/stream`.
