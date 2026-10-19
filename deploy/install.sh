#!/usr/bin/env bash
# Install on Raspberry Pi OS Lite (run as root). Simulation mode only in this release.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
id gt86rs >/dev/null 2>&1 || useradd --system --home /var/lib/gt86-remote-start --shell /usr/sbin/nologin gt86rs
apt-get install -y python3-venv
install -d -o gt86rs -g gt86rs /var/lib/gt86-remote-start /var/lib/gt86-remote-start/logs
install -d /etc/gt86-remote-start /opt/gt86-remote-start
python3 -m venv /opt/gt86-remote-start/venv
/opt/gt86-remote-start/venv/bin/pip install --upgrade pip
/opt/gt86-remote-start/venv/bin/pip install "$SRC[hardware]"
[ -f /etc/gt86-remote-start/config.yaml ] || {
  install -m 0644 "$SRC/config.yaml" /etc/gt86-remote-start/config.yaml
  sed -i 's#directory: logs#directory: /var/lib/gt86-remote-start/logs#' /etc/gt86-remote-start/config.yaml
}
/opt/gt86-remote-start/venv/bin/gt86-remote-start check-config --config /etc/gt86-remote-start/config.yaml
install -m 0644 "$SRC/deploy/gt86-remote-start.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gt86-remote-start
echo "Installed. Logs: journalctl -u gt86-remote-start -f ; status: /var/lib/gt86-remote-start/logs/status.json"
