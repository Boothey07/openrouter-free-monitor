#!/bin/bash
# install.sh — set up openrouter-fm on a fresh host
#
# Usage:
#   sudo ./scripts/install.sh
#
# This will:
#   1. Install the Python package via pip
#   2. Create /var/lib/openrouter-fm with correct permissions
#   3. Install systemd unit files to /etc/systemd/system
#   4. Enable and start the hourly poll + daily health timers
#
# You must populate /etc/openrouter-fm.env before the timers fire.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Must run as root (sudo $0)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Installing Python package (editable)"
pip install -e "$REPO_DIR"

echo "==> Creating state directory"
mkdir -p /var/lib/openrouter-fm /var/log
chmod 755 /var/lib/openrouter-fm

echo "==> Installing systemd units"
install -m 0644 "$REPO_DIR/systemd/openrouter-fm-poll.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/systemd/openrouter-fm-poll.timer" /etc/systemd/system/
install -m 0644 "$REPO_DIR/systemd/openrouter-fm-health.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/systemd/openrouter-fm-health.timer" /etc/systemd/system/

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Enabling + starting timers"
systemctl enable --now openrouter-fm-poll.timer
systemctl enable --now openrouter-fm-health.timer

echo ""
echo "==> Verifying timer schedule"
systemctl list-timers openrouter-fm-* || true

echo ""
echo "Installed. Next steps:"
echo "  1. Create /etc/openrouter-fm.env with:"
echo "     OPENROUTER_API_KEY=sk-or-v1-..."
echo "     TELEGRAM_BOT_TOKEN=..."
echo "     TELEGRAM_CHAT_ID=..."
echo "  2. Run: openrouter-fm init"
echo "  3. Run: openrouter-fm poll --no-alert   (sanity check)"
echo "  4. Watch /var/log/openrouter-fm.log"
