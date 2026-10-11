#!/usr/bin/env bash
# Minimal auris service calls. Requires: AURIS_BASE_URL, AURIS_AUTH_TOKEN.
#
#   export AURIS_BASE_URL=http://<host>:<port>
#   export AURIS_AUTH_TOKEN=<token>
#   ./examples/curl.sh
set -euo pipefail
: "${AURIS_BASE_URL:?set AURIS_BASE_URL e.g. http://host:8011}"
: "${AURIS_AUTH_TOKEN:?set AURIS_AUTH_TOKEN}"
AUTH=(-H "Authorization: Bearer ${AURIS_AUTH_TOKEN}")

echo "== voices =="
curl -fsS "${AUTH[@]}" "${AURIS_BASE_URL}/v1/voices"; echo

echo "== clone (expects ref.wav in cwd) =="
curl -fsS "${AUTH[@]}" -F file=@ref.wav -F "ref_text=the words spoken in ref.wav" \
  "${AURIS_BASE_URL}/v1/voices/MyVoice/clone"; echo

echo "== synthesise -> out.mp3 (default format is mp3) =="
curl -fsS "${AUTH[@]}" -H 'content-type: application/json' \
  -d '{"voice":"MyVoice","text":"Hello from curl."}' \
  "${AURIS_BASE_URL}/v1/tts" --output out.mp3
echo "wrote out.mp3"
