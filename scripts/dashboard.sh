#!/usr/bin/env bash
#
# Open the auris dashboard in your browser. The first run asks for the service URL and your token
# and saves them to client.env (gitignored) so you never retype them. No Python needed — just
# curl and a browser.
#
# Usage:  ./scripts/dashboard.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."
CONF=client.env

if [ ! -f "$CONF" ]; then
  echo "First-time setup — enter the details your team gave you."
  read -r -p "  Service URL (e.g. http://your-server:8011): " BASE
  read -r -p "  Auth token: " TOKEN
  BASE="${BASE%/}"
  ( umask 177; printf 'AURIS_BASE_URL=%s\nAURIS_AUTH_TOKEN=%s\n' "$BASE" "$TOKEN" >"$CONF" )
  echo "  Saved $CONF (gitignored). Edit it any time to change the URL or token."
  echo
fi

# load saved settings (KEY=value lines) and export them for the examples too
set -a; . "./$CONF"; set +a
: "${AURIS_BASE_URL:?client.env is missing AURIS_BASE_URL}"

echo "Checking ${AURIS_BASE_URL} ..."
if curl -fsS -m 5 "${AURIS_BASE_URL}/ready" >/dev/null 2>&1; then
  echo "  Service is up."
else
  echo "  WARNING: couldn't reach ${AURIS_BASE_URL}/ready — opening anyway (check the URL / your VPN)."
fi

URL="${AURIS_BASE_URL}/"
echo "Opening ${URL}"
if command -v open >/dev/null 2>&1; then open "$URL"             # macOS
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"   # Linux
else echo "Open this in your browser: $URL"; fi
