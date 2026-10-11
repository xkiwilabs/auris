#!/usr/bin/env bash
#
# Set up auris as a GPU TTS service on a fresh NVIDIA / Ubuntu workstation.
#
# Creates ./.venv with a GPU-capable PyTorch, installs auris (CLI + server + f5-TTS),
# and writes ./.env with a generated auth token + your Tailscale bind address.
#
# Run WITHOUT sudo — it only touches this repo's ./.venv and ./.env.
# Prerequisites: uv (https://astral.sh/uv), an NVIDIA driver, and ffmpeg (apt install ffmpeg).
#
set -euo pipefail
cd "$(dirname "$0")/.."

# cu128 wheels include kernels for Blackwell (sm_120, e.g. RTX PRO 6000) AND Ada (sm_89, e.g.
# RTX 4090). For an older driver that can't do CUDA 12.8, override, e.g.:
#   AURIS_TORCH_INDEX=https://download.pytorch.org/whl/cu124 scripts/setup-workstation.sh
TORCH_INDEX="${AURIS_TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
PORT="${AURIS_PORT:-8011}"
GPU_DEVICES="${AURIS_GPU_DEVICES:-cuda:0}"
GPU_WORKERS="${AURIS_GPU_WORKERS:-2}"

echo "==> auris workstation setup"

# 1. prerequisites
command -v uv >/dev/null 2>&1 || {
  echo "ERROR: 'uv' not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
}
command -v nvidia-smi >/dev/null 2>&1 || {
  echo "ERROR: nvidia-smi not found — this script targets an NVIDIA GPU box." >&2
  exit 1
}
command -v ffmpeg >/dev/null 2>&1 || echo "WARNING: ffmpeg not found — f5-TTS needs it (sudo apt install ffmpeg)"
command -v espeak-ng >/dev/null 2>&1 || echo "WARNING: espeak-ng not found — Kokoro presets need it (sudo apt install espeak-ng)"
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1

# 2. virtualenv
if [ -d .venv ]; then echo "==> reusing existing .venv"; else uv venv .venv --python 3.11; fi

# 3. GPU-capable torch FIRST, then the project (so f5-TTS doesn't pull a CPU/old torch)
echo "==> installing torch + torchaudio from ${TORCH_INDEX}"
VIRTUAL_ENV=.venv uv pip install torch torchaudio --index-url "$TORCH_INDEX"
echo "==> installing auris[dev,server,model,kokoro] (f5-TTS, faster-whisper, Kokoro, FastAPI)"
VIRTUAL_ENV=.venv uv pip install -e ".[dev,server,model,kokoro]"

# 4. verify the GPU actually computes (catches driver/arch mismatches early)
.venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available(), "CUDA not available to torch"
x = torch.randn(512, 512, device="cuda")
(x @ x).sum().item()
print(f"GPU OK: {torch.cuda.get_device_name(0)}  (torch {torch.__version__})")
PY

# 5. .env (never overwrite an existing one)
if [ -f .env ]; then
  echo "==> .env already exists — leaving it untouched"
else
  TOKEN=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))")
  TSIP=$(tailscale ip -4 2>/dev/null | head -1 || true)
  BIND="${TSIP:-0.0.0.0}"
  if [ -z "$TSIP" ]; then
    echo "NOTE: no Tailscale IP found — binding ${BIND} (all interfaces)."
    echo "      For private tailnet-only access: install/login tailscale, then set AURIS_BIND_HOST in .env."
  fi
  umask 077
  cat > .env <<EOF
# auris service config (loaded automatically; keep the token secret — .env is gitignored)
AURIS_AUTH_TOKEN=$TOKEN
AURIS_BIND_HOST=$BIND
AURIS_PORT=$PORT
AURIS_DATA_DIR=$(pwd)/data
AURIS_GPU_DEVICES=$GPU_DEVICES
AURIS_GPU_WORKERS=$GPU_WORKERS
EOF
  chmod 600 .env
  echo "==> wrote .env  (token generated; bind ${BIND}:${PORT}; ${GPU_DEVICES} x${GPU_WORKERS})"
fi
mkdir -p data

cat <<EOF

Done. Your auth token is in ./.env  (read it with:  grep AURIS_AUTH_TOKEN .env).

Next:
  scripts/install-service.sh     # run it as a persistent systemd service (uses sudo)
  scripts/test-service.sh        # smoke-test once it's running
  # or run in the foreground for a quick try:
  .venv/bin/auris-server
EOF
