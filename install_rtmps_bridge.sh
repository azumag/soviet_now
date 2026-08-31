#!/bin/bash
# Install a loopback-only RTMPS egress bridge for the nginx-rtmp relay.
#
# nginx-rtmp's `push` speaks plain RTMP only. Kick's ingest (Amazon IVS) accepts
# RTMPS on 443 and rejects plain RTMP on 1935, so the relay pushes to a loopback
# port that stunnel wraps in TLS. The destination stream key stays in
# /etc/soren-rtmp/push.conf and is never read by this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TEMPLATE_SOURCE="$SCRIPT_DIR/deploy/soren-rtmp/rtmps-bridge.conf.template"
UNIT_SOURCE="$SCRIPT_DIR/deploy/soren-rtmp/soren-rtmps-bridge.service"
CONFIG_DIR="/etc/soren-rtmp"
CONFIG_TARGET="$CONFIG_DIR/rtmps-bridge.conf"
UNIT_TARGET="/etc/systemd/system/soren-rtmps-bridge.service"
SERVICE="soren-rtmps-bridge.service"

BRIDGE_NAME="kick"
LOCAL_PORT="19351"
REMOTE_HOST=""
REMOTE_PORT="443"
MODE=""
CONFIRM=0

usage() {
	cat >&2 <<'USAGE'
Usage:
  install_rtmps_bridge.sh --status
  install_rtmps_bridge.sh --print-config
  install_rtmps_bridge.sh --install --host <ingest-host> [--name kick]
                          [--local-port 19351] [--remote-port 443]
                          --confirm-package-install

The ingest host is operator-supplied (it identifies the destination channel) and
is deliberately not stored in this repository. After installing, add the matching
push line to /etc/soren-rtmp/push.conf with sudoedit:

  push rtmp://127.0.0.1:<local-port>/app/<STREAM_KEY>;
USAGE
	exit 2
}

while [ $# -gt 0 ]; do
	case "$1" in
	--status | --print-config | --install) MODE="${1#--}" ;;
	--confirm-package-install) CONFIRM=1 ;;
	--name)
		BRIDGE_NAME="${2:-}"
		shift
		;;
	--host)
		REMOTE_HOST="${2:-}"
		shift
		;;
	--local-port)
		LOCAL_PORT="${2:-}"
		shift
		;;
	--remote-port)
		REMOTE_PORT="${2:-}"
		shift
		;;
	*) usage ;;
	esac
	shift
done
[ -n "$MODE" ] || MODE="status"

# The rendered config is the source of truth for an installed bridge, so status
# and print-config read the listen port back from it instead of the defaults.
# It is root:soren-relay 0640, so a plain read only works for the relay group;
# fall back to a non-interactive sudo and then to the requested default. This
# must never fail the caller, which runs under `set -e`.
installed_local_port() {
	local port=""
	if [ -r "$CONFIG_TARGET" ]; then
		port=$(awk -F'[:= ]+' '/^accept/ { print $NF }' "$CONFIG_TARGET" 2>/dev/null | head -n1)
	elif command -v sudo >/dev/null 2>&1; then
		port=$(sudo -n awk -F'[:= ]+' '/^accept/ { print $NF }' "$CONFIG_TARGET" 2>/dev/null | head -n1)
	fi
	printf '%s' "$port"
	return 0
}

print_config() {
	local port
	port="$(installed_local_port)"
	[ -n "$port" ] || port="$LOCAL_PORT"
	python3 - "$CONFIG_TARGET" "$UNIT_TARGET" "$port" <<'PY'
import json
import sys

print(json.dumps({
    "config": sys.argv[1],
    "unit": sys.argv[2],
    "listen": "127.0.0.1:" + sys.argv[3],
    "service": "soren-rtmps-bridge.service",
    "service_user": "soren-relay",
    "stream_keys_in_bridge_config": False,
    "stream_keys_in_process_args": False,
}, sort_keys=True))
PY
}

status() {
	local port
	print_config
	if command -v systemctl >/dev/null 2>&1; then
		systemctl is-active "$SERVICE" 2>/dev/null || true
	fi
	port="$(installed_local_port)"
	[ -n "$port" ] || port="$LOCAL_PORT"
	if command -v ss >/dev/null 2>&1; then
		ss -ltn 2>/dev/null | awk -v want="127.0.0.1:$port" 'NR == 1 || $4 == want'
	fi
}

case "$MODE" in
print-config)
	print_config
	exit 0
	;;
status)
	status
	exit 0
	;;
install)
	[ "$CONFIRM" -eq 1 ] || {
		echo "refusing package/service mutation without --confirm-package-install" >&2
		exit 2
	}
	;;
*) usage ;;
esac

[ -n "$REMOTE_HOST" ] || {
	echo "--install requires --host <ingest-host>" >&2
	exit 2
}
case "$REMOTE_HOST" in
*[!A-Za-z0-9.-]* | -* | "")
	echo "invalid ingest host: $REMOTE_HOST" >&2
	exit 2
	;;
esac
case "$BRIDGE_NAME" in
*[!A-Za-z0-9_-]* | "")
	echo "invalid bridge name: $BRIDGE_NAME" >&2
	exit 2
	;;
esac
for port in "$LOCAL_PORT" "$REMOTE_PORT"; do
	case "$port" in
	'' | *[!0-9]*)
		echo "invalid port: $port" >&2
		exit 2
		;;
	esac
	[ "$port" -ge 1 ] && [ "$port" -le 65535 ] || {
		echo "port out of range: $port" >&2
		exit 2
	}
done

if [ "$(uname -s)" != "Linux" ]; then
	echo "RTMPS bridge installation is Linux-only" >&2
	exit 2
fi
for file in "$TEMPLATE_SOURCE" "$UNIT_SOURCE"; do
	[ -f "$file" ] || {
		echo "missing bridge asset: $file" >&2
		exit 2
	}
done
for command in sudo apt-get systemctl getent install; do
	command -v "$command" >/dev/null 2>&1 || {
		echo "required command not found: $command" >&2
		exit 2
	}
done
sudo -n true

sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y stunnel4

# The distro unit reads every /etc/stunnel/*.conf and is not used by this
# bridge. A dedicated hardened unit runs the same binary with its own config.
sudo systemctl disable --now stunnel4.service >/dev/null 2>&1 || true

STUNNEL_BIN=""
for candidate in /usr/bin/stunnel /usr/bin/stunnel4; do
	[ -x "$candidate" ] && STUNNEL_BIN="$candidate" && break
done
[ -n "$STUNNEL_BIN" ] || {
	echo "stunnel binary not found after install" >&2
	exit 1
}

# The bridge shares the relay's unprivileged service account so the two units
# have exactly the same (empty) privilege set.
getent passwd soren-relay >/dev/null 2>&1 || {
	echo "soren-relay service account is missing; run install_direct_stream_relay.sh first" >&2
	exit 2
}
sudo install -d -o root -g soren-relay -m 0750 "$CONFIG_DIR"

render_tmp=$(mktemp "$SCRIPT_DIR/tmp/.soren-rtmps-bridge.XXXXXX")
trap 'rm -f "$render_tmp"' EXIT
sed \
	-e "s|__BRIDGE_NAME__|$BRIDGE_NAME|g" \
	-e "s|__LOCAL_PORT__|$LOCAL_PORT|g" \
	-e "s|__REMOTE_HOST__|$REMOTE_HOST|g" \
	-e "s|__REMOTE_PORT__|$REMOTE_PORT|g" \
	"$TEMPLATE_SOURCE" >"$render_tmp"
sudo install -o root -g soren-relay -m 0640 "$render_tmp" "$CONFIG_TARGET"
rm -f "$render_tmp"
trap - EXIT

sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
if [ "$STUNNEL_BIN" != "/usr/bin/stunnel" ]; then
	sudo sed -i "s|^ExecStart=.*|ExecStart=$STUNNEL_BIN $CONFIG_TARGET|" "$UNIT_TARGET"
fi
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"

for _ in 1 2 3 4 5 6 7 8 9 10; do
	ss -ltn 2>/dev/null | awk -v want="127.0.0.1:$LOCAL_PORT" '$4 == want { found = 1 } END { exit !found }' && break
	sleep 1
done
if ! ss -ltn 2>/dev/null | awk -v want="127.0.0.1:$LOCAL_PORT" '$4 == want { found = 1 } END { exit !found }'; then
	echo "bridge did not bind to loopback 127.0.0.1:$LOCAL_PORT" >&2
	exit 1
fi
status
