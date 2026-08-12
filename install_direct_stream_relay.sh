#!/bin/bash
# Install a dedicated, unprivileged, loopback-only nginx-rtmp relay. Destination
# credentials are deliberately kept out of this repository and Soren's .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:---status}"
CONFIRM=0
if [ "${2:-}" = "--confirm-package-install" ]; then
	CONFIRM=1
fi

CONFIG_SOURCE="$SCRIPT_DIR/deploy/soren-rtmp/nginx.conf"
UNIT_SOURCE="$SCRIPT_DIR/deploy/soren-rtmp/soren-rtmp-relay.service"
CONFIG_DIR="/etc/soren-rtmp"
CONFIG_TARGET="$CONFIG_DIR/nginx.conf"
PUSH_TARGET="$CONFIG_DIR/push.conf"
UNIT_TARGET="/etc/systemd/system/soren-rtmp-relay.service"

usage() {
	echo "Usage: $0 --status | --print-config | --install --confirm-package-install" >&2
	exit 2
}

print_config() {
	python3 - "$CONFIG_TARGET" "$PUSH_TARGET" "$UNIT_TARGET" <<'PY'
import json
import sys

print(json.dumps({
    "listen": "127.0.0.1:1935",
    "application": "soren",
    "config": sys.argv[1],
    "push_config": sys.argv[2],
    "unit": sys.argv[3],
    "service_user": "soren-relay",
    "destination_credentials_in_soren_env": False,
    "destination_credentials_in_process_args": False,
}, sort_keys=True))
PY
}

status() {
	print_config
	if command -v systemctl >/dev/null 2>&1; then
		systemctl is-active soren-rtmp-relay.service 2>/dev/null || true
	fi
	if command -v ss >/dev/null 2>&1; then
		ss -ltn 2>/dev/null | awk 'NR == 1 || $4 == "127.0.0.1:1935"'
	fi
}

case "$MODE" in
--print-config)
	print_config
	exit 0
	;;
--status)
	status
	exit 0
	;;
--install)
	[ "$CONFIRM" -eq 1 ] || {
		echo "refusing package/service mutation without --confirm-package-install" >&2
		exit 2
	}
	;;
*) usage ;;
esac

if [ "$(uname -s)" != "Linux" ]; then
	echo "RTMP relay installation is Linux-only" >&2
	exit 2
fi
for file in "$CONFIG_SOURCE" "$UNIT_SOURCE"; do
	[ -f "$file" ] || {
		echo "missing relay asset: $file" >&2
		exit 2
	}
done
for command in sudo apt-get systemctl getent useradd install; do
	command -v "$command" >/dev/null 2>&1 || {
		echo "required command not found: $command" >&2
		exit 2
	}
done
sudo -n true

sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y nginx libnginx-mod-rtmp

# The distro nginx service would expose its default HTTP site and is not used by
# this relay. A dedicated hardened unit runs the same binary with its own config.
sudo systemctl disable --now nginx.service >/dev/null 2>&1 || true
if ! getent passwd soren-relay >/dev/null 2>&1; then
	sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin soren-relay
fi
sudo install -d -o root -g soren-relay -m 0750 "$CONFIG_DIR"
sudo install -o root -g root -m 0644 "$CONFIG_SOURCE" "$CONFIG_TARGET"
if [ ! -e "$PUSH_TARGET" ]; then
	push_tmp=$(mktemp "$SCRIPT_DIR/tmp/.soren-rtmp-push.XXXXXX")
	: >"$push_tmp"
	sudo install -o root -g soren-relay -m 0640 "$push_tmp" "$PUSH_TARGET"
	rm -f "$push_tmp"
else
	sudo chown root:soren-relay "$PUSH_TARGET"
	sudo chmod 0640 "$PUSH_TARGET"
fi
sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
sudo systemctl daemon-reload
# RuntimeDirectory is normally created by systemd before ExecStartPre. Create
# the same exact directory once here so the pre-enable nginx -t has a pid path.
sudo install -d -o soren-relay -g soren-relay -m 0750 /run/soren-rtmp-relay
sudo -u soren-relay /usr/sbin/nginx -t -c "$CONFIG_TARGET"
sudo systemctl enable --now soren-rtmp-relay.service

if ! ss -ltn 2>/dev/null | awk '$4 == "127.0.0.1:1935" { found=1 } END { exit !found }'; then
	echo "relay did not bind to loopback 127.0.0.1:1935" >&2
	exit 1
fi
status
