#!/usr/bin/env bash
#
# Manage the auris-server auth token — the single shared token the service accepts.
#
#   scripts/token.sh show          # print the current token (to hand to a new user)
#   scripts/token.sh set           # set a token, read from a hidden prompt or stdin
#   scripts/token.sh rotate        # generate a NEW token, restart the service, print it
#
# Reads/writes ./.env (override the path with AURIS_ENV_FILE, e.g. for tests).
#
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${AURIS_ENV_FILE:-.env}"

_get() { grep -m1 '^AURIS_AUTH_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true; }

_set() {
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  local tmp
  # Keep the temp file beside .env (not world-readable /tmp) so the secret never lands there.
  tmp="$(mktemp "$(dirname "$ENV_FILE")/.token.XXXXXX")"
  grep -v '^AURIS_AUTH_TOKEN=' "$ENV_FILE" >"$tmp" 2>/dev/null || true
  printf 'AURIS_AUTH_TOKEN=%s\n' "$1" >>"$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

case "${1:-}" in
  show)
    tok="$(_get)"
    if [ -n "$tok" ]; then echo "$tok"; else
      echo "no AURIS_AUTH_TOKEN in $ENV_FILE" >&2; exit 1
    fi
    ;;
  set)
    # Read the token without exposing it on the command line / process listing.
    if [ -t 0 ]; then
      read -r -s -p "New token: " tok; echo
    else
      tok="$(cat)"
    fi
    [ -n "$tok" ] || { echo "empty token" >&2; exit 2; }
    _set "$tok"
    echo "Token set in $ENV_FILE."
    ;;
  rotate)
    echo "This generates a NEW token and INVALIDATES every current user's token."
    read -r -p "Type 'yes' to rotate: " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    new="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    _set "$new"
    echo "New token set. Restarting the service so it takes effect…"
    if command -v systemctl >/dev/null 2>&1; then
      sudo systemctl restart auris-server 2>/dev/null \
        || echo "(couldn't restart automatically — run: sudo scripts/service.sh restart)"
    fi
    echo
    echo "New token (send this to your users):"
    echo "    $new"
    echo "Every existing user must update their token."
    ;;
  *)
    echo "usage: token.sh {show | set | rotate}   (set reads the token from stdin)" >&2
    exit 2
    ;;
esac
