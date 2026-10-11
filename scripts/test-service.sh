#!/usr/bin/env bash
#
# Smoke-test the running auris service end to end: clone a throwaway voice, synthesise,
# then delete the voice. Reads config (incl. the auth token) from ./.env.
#
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "ERROR: no ./.env — run scripts/setup-workstation.sh first." >&2; exit 1; }

# load .env into the environment
set -a; . ./.env; set +a
HOST="${AURIS_BIND_HOST:-127.0.0.1}"; PORT="${AURIS_PORT:-8011}"
BASE="http://${HOST}:${PORT}"
AUTH=(-H "Authorization: Bearer ${AURIS_AUTH_TOKEN}")

echo "==> ${BASE}/ready"
curl -fsS -m 5 "${BASE}/ready"; echo

echo "==> making a 5s throwaway reference clip"
.venv/bin/python -c "import numpy as np,soundfile as sf;t=np.linspace(0,5,120000,dtype='f4');sf.write('/tmp/auris_ref.wav',0.1*np.sin(2*np.pi*140*t),24000)"

echo "==> clone voice '_smoketest'"
curl -fsS -m 90 "${AUTH[@]}" \
  -F file=@/tmp/auris_ref.wav -F "ref_text=a throwaway smoke test reference recording" \
  "${BASE}/v1/voices/_smoketest/clone"; echo

echo "==> synthesise (default format is mp3; pass \"format\":\"wav\" for WAV)"
curl -fsS -m 90 "${AUTH[@]}" -H 'content-type: application/json' \
  -d '{"voice":"_smoketest","text":"The auris speech service is working."}' \
  "${BASE}/v1/tts" --output /tmp/auris_out.mp3
echo "==> got $(stat -c%s /tmp/auris_out.mp3) bytes of audio -> /tmp/auris_out.mp3"

echo "==> cleanup: delete the smoke-test voice"
curl -fsS -m 10 -X DELETE "${AUTH[@]}" "${BASE}/v1/voices/_smoketest" >/dev/null && echo "done — service is healthy."
