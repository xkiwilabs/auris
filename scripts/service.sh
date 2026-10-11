#!/usr/bin/env bash
#
# Manage the auris-server systemd service: start | stop | restart | status | logs
#
set -euo pipefail
cmd="${1:-status}"
case "$cmd" in
  start)   sudo systemctl start auris-server ;;
  stop)    sudo systemctl stop auris-server ;;
  restart) sudo systemctl restart auris-server ;;
  status)  systemctl status auris-server --no-pager ;;
  logs)    journalctl -u auris-server -f ;;        # live tail (Ctrl-C to exit)
  *) echo "usage: $0 {start|stop|restart|status|logs}" >&2; exit 2 ;;
esac
