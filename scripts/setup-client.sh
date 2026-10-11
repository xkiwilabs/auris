#!/usr/bin/env bash
#
# Set up the auris REMOTE-CLIENT profile on your own machine (macOS or Ubuntu/Linux) — no GPU,
# no model. You get the `auris` CLI in remote mode (record / tts / say / voices against a running
# service) and `auris dashboard` (a localhost dashboard where the mic works).
#
# Usage:
#   ./scripts/setup-client.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "ERROR: '$PY' not found. Install Python 3.11+ (macOS: brew install python; Ubuntu: sudo apt install python3 python3-venv)." >&2
  exit 1
}

# The mic (for `auris record`) needs PortAudio at the system level:
command -v brew >/dev/null 2>&1 && BREW=1 || BREW=0
if ! "$PY" -c "import ctypes.util,sys; sys.exit(0 if ctypes.util.find_library('portaudio') else 1)" 2>/dev/null; then
  echo "NOTE: PortAudio not found — needed for mic recording. Install it:"
  [ "$BREW" = 1 ] && echo "  brew install portaudio" || echo "  sudo apt install libportaudio2"
fi

echo "==> creating .venv-client"
"$PY" -m venv .venv-client
.venv-client/bin/python -m pip install --quiet --upgrade pip
echo "==> installing auris client (no model) + example deps"
.venv-client/bin/python -m pip install --quiet -e ".[client,audio]" requests openai

echo
echo "==> configuring the service (you can skip the token and add it later)"
.venv-client/bin/auris login || true

cat <<'EOF'

Client ready. Activate it in each new shell with:  source .venv-client/bin/activate

Then:
  auris dashboard                 # open the dashboard locally (mic works here)
  auris voices                    # list the service's voices
  auris record "Me"               # record from your mic and upload (clone your voice)
  auris tts narrator-uk-f --text "Hello." --out hello.mp3

Add or change your token any time:  auris config token
For agents/automation, set AURIS_BASE_URL and AURIS_AUTH_TOKEN in the environment instead.
EOF
