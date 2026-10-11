#!/usr/bin/env bash
#
# auris setup — creates a Python 3.11 virtualenv at ./.venv and installs auris.
#
# Usage:
#   scripts/install.sh            # library + CLI + test suite (no model weights)
#   scripts/install.sh --model    # also install torch + f5-TTS (multi-GB) for real inference
#   scripts/install.sh --server   # also install FastAPI/uvicorn for the HTTP service
#
set -euo pipefail

# Run from the repo root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

VENV=".venv"
EXTRAS="dev,audio"

for arg in "$@"; do
  case "$arg" in
    --model) EXTRAS="$EXTRAS,model,kokoro" ;;
    --server) EXTRAS="$EXTRAS,server" ;;
    -h|--help)
      sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [[ "$EXTRAS" == *model* ]]; then
  echo "==> Note: --model pulls torch + f5-TTS (several GB). This can take a few minutes."
fi

if command -v uv >/dev/null 2>&1; then
  if [ -d "$VENV" ]; then
    echo "==> Reusing existing virtualenv at $VENV"
  else
    echo "==> Creating virtualenv at $VENV (uv, Python 3.11)"
    uv venv --python 3.11 "$VENV"
  fi
  echo "==> Installing auris[$EXTRAS]"
  uv pip install --python "$VENV" -e ".[$EXTRAS]"
else
  # No uv: find a system Python that is 3.11+ (the package requires it).
  PY=""
  for cand in python3.11 python3.12 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "error: need 'uv' or a Python 3.11+ interpreter on PATH." >&2
    echo "       install uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
  if [ -d "$VENV" ]; then
    echo "==> Reusing existing virtualenv at $VENV"
  else
    echo "==> Creating virtualenv at $VENV ($PY)"
    "$PY" -m venv "$VENV"
  fi
  echo "==> Installing auris[$EXTRAS]"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -e ".[$EXTRAS]"
fi

# Sanity check: the package imports and the CLI is wired.
"$VENV/bin/python" -c "import auris; print('auris', auris.__version__, 'installed')"

# f5-TTS decodes audio through FFmpeg (torchaudio) — warn if it's missing.
if [[ "$EXTRAS" == *model* ]] && ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "WARNING: ffmpeg not found on PATH — 'auris tts' will fail without it."
  echo "  macOS: brew install ffmpeg     Linux: sudo apt install ffmpeg"
fi

echo
echo "Done. Next:"
echo "  source $VENV/bin/activate"
echo "  auris --help"
if [[ "$EXTRAS" != *model* ]]; then
  echo
  echo "For real voice cloning (torch + f5-TTS), re-run: scripts/install.sh --model"
fi
