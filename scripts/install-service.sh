#!/usr/bin/env bash
#
# Install auris-server as a persistent systemd service (auto-start on boot, restart on failure).
# Generates the unit for THIS user + repo path. Run it with OR without sudo — it only uses sudo
# for the systemd parts, and resolves the real (non-root) user either way.
#
# Prereq: run scripts/setup-workstation.sh first (creates ./.venv and ./.env).
#
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
# When invoked via `sudo`, $SUDO_USER is the real user; fall back to id -un otherwise.
# The service must NOT run as root (it would write root-owned files into data/ and re-download
# the model into root's cache).
USER_NAME="${SUDO_USER:-$(id -un)}"
if [ "$USER_NAME" = "root" ]; then
  echo "ERROR: refusing to install a service that runs as root. Run as a normal user (sudo is fine)." >&2
  exit 1
fi
UNIT=/etc/systemd/system/auris-server.service

[ -f .env ] || { echo "ERROR: no ./.env — run scripts/setup-workstation.sh first." >&2; exit 1; }
[ -x .venv/bin/auris-server ] || { echo "ERROR: .venv/bin/auris-server missing — run scripts/setup-workstation.sh first." >&2; exit 1; }

echo "==> writing ${UNIT}  (User=${USER_NAME}, WorkingDirectory=${REPO})"
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=auris TTS service (f5-TTS, GPU)
# Order after tailscale so the tailnet IP exists before we bind to it.
After=network-online.target tailscaled.service
Wants=network-online.target tailscaled.service

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${REPO}
EnvironmentFile=${REPO}/.env
Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
Environment=HF_HUB_DISABLE_TELEMETRY=1
ExecStart=${REPO}/.venv/bin/auris-server
Restart=on-failure
RestartSec=5
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
EOF

# Free the port before (re)starting: stop any systemd-managed instance AND kill any stray
# foreground/nohup run. Match the venv path loosely so BOTH absolute and relative cmdlines
# are caught (a `nohup .venv/bin/auris-server` shows a relative path).
sudo systemctl stop auris-server 2>/dev/null || true
pkill -f "venv/bin/auris-server" 2>/dev/null && echo "==> stopped a stray (non-systemd) instance" || true
sleep 1

sudo systemctl daemon-reload
sudo systemctl enable auris-server          # start on boot
sudo systemctl start auris-server           # start now, applying the current unit
echo "==> waiting ~15s for f5-TTS to load + warm on the GPU..."
sleep 15
sudo systemctl status auris-server --no-pager --lines=6 || true

cat <<EOF

Done. Manage it with:  scripts/service.sh {status|logs|restart|stop}
Health check:           curl http://\$(grep AURIS_BIND_HOST .env | cut -d= -f2):\$(grep AURIS_PORT .env | cut -d= -f2)/ready
EOF
