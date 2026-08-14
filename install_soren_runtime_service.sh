#!/bin/bash
# Install the boot-persistent Soren supervisor and safely migrate the currently
# running manual supervisor. OBS is not restarted during this migration.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUNTIME_UNIT="soren-runtime.service"
RUNTIME_UNIT_SOURCE="$SCRIPT_DIR/deploy/soren-runtime/soren-runtime.service"
RUNTIME_UNIT_TARGET="/etc/systemd/system/$RUNTIME_UNIT"
OBS_DROPIN_SOURCE="$SCRIPT_DIR/deploy/soren-runtime/obs-backend.conf"
OBS_DROPIN_TARGET="/etc/systemd/system/obs.service.d/stream-backend.conf"
EXPECTED_REPO="${SOREN_RUNTIME_REPO:-/home/ubuntu/soren}"
EXPECTED_USER="${SOREN_RUNTIME_USER:-ubuntu}"
EXPECTED_UID="${SOREN_RUNTIME_UID:-1001}"
ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"

usage() {
	echo "Usage: $0 --status | --print-config | --install --confirm-runtime-migration" >&2
	exit 2
}

backend_name() {
	if SOREN_ENV_FILE="$ENV_FILE" ./stream_backend_condition.sh obs >/dev/null 2>&1; then
		printf '%s\n' obs
	elif SOREN_ENV_FILE="$ENV_FILE" ./stream_backend_condition.sh ffmpeg >/dev/null 2>&1; then
		printf '%s\n' ffmpeg
	else
		printf '%s\n' invalid
	fi
}

print_config() {
	python3 - "$EXPECTED_REPO" "$RUNTIME_UNIT_TARGET" "$OBS_DROPIN_TARGET" "$EXPECTED_USER" "$EXPECTED_UID" "$(backend_name)" <<'PY'
import json
import sys

print(json.dumps({
    "repo": sys.argv[1],
    "runtime_unit": sys.argv[2],
    "obs_backend_dropin": sys.argv[3],
    "runtime_user": sys.argv[4],
    "runtime_uid": int(sys.argv[5]),
    "selected_backend": sys.argv[6],
    "pulse_sink": "soren_null",
    "stream_credentials_read": False,
    "obs_restarted_during_migration": False,
}, sort_keys=True))
PY
}

status() {
	print_config
	if command -v systemctl >/dev/null 2>&1; then
		printf 'runtime_enabled=%s\n' "$(systemctl is-enabled "$RUNTIME_UNIT" 2>/dev/null || true)"
		printf 'runtime_active=%s\n' "$(systemctl is-active "$RUNTIME_UNIT" 2>/dev/null || true)"
		printf 'obs_active=%s\n' "$(systemctl is-active obs.service 2>/dev/null || true)"
	fi
}

case "$*" in
--print-config)
	print_config
	exit 0
	;;
--status)
	status
	exit 0
	;;
--install\ --confirm-runtime-migration) ;;
*) usage ;;
esac

if [ "$(uname -s)" != "Linux" ]; then
	echo "runtime service installation is Linux-only" >&2
	exit 2
fi
if [ "$SCRIPT_DIR" != "$EXPECTED_REPO" ]; then
	echo "runtime repository path does not match the service contract" >&2
	exit 2
fi
if [ "$(id -u "$EXPECTED_USER" 2>/dev/null || true)" != "$EXPECTED_UID" ]; then
	echo "runtime user identity does not match the service contract" >&2
	exit 2
fi
for file in \
	"$RUNTIME_UNIT_SOURCE" \
	"$OBS_DROPIN_SOURCE" \
	stream_backend_condition.sh \
	wait_soren_runtime_prereqs.sh \
	start_all.sh \
	stop_soren.sh \
	soviet_watchdog.sh \
	soviet_local.mjs \
	generate_status_overlay.sh \
	generate_show_status_overlay.sh \
	obs_control.sh \
	"$ENV_FILE"; do
	[ -f "$file" ] || {
		echo "missing runtime asset" >&2
		exit 2
	}
done
for command_name in sudo systemctl loginctl install pactl xdpyinfo python3 node lsof tmux; do
	command -v "$command_name" >/dev/null 2>&1 || {
		echo "missing runtime installation command: $command_name" >&2
		exit 2
	}
done
if [ "$(backend_name)" = invalid ]; then
	echo "runtime backend is invalid" >&2
	exit 2
fi
if [ -e tmp/stop ]; then
	echo "runtime stop sentinel is present; refusing to migrate a deliberately stopped runtime" >&2
	exit 2
fi
sudo -n true

OBS_WAS_LIVE=0
if systemctl is-active --quiet obs.service \
	&& [ "$(./obs_control.sh stream-status 2>/dev/null || true)" = "streaming=on" ]; then
	OBS_WAS_LIVE=1
fi
RUNTIME_WAS_ACTIVE=0
if systemctl is-active --quiet "$RUNTIME_UNIT"; then
	RUNTIME_WAS_ACTIVE=1
fi
LINGER_WAS_ENABLED=0
if [ "$(loginctl show-user "$EXPECTED_USER" -p Linger --value 2>/dev/null || true)" = yes ]; then
	LINGER_WAS_ENABLED=1
fi

chmod 0755 stream_backend_condition.sh wait_soren_runtime_prereqs.sh
sudo loginctl enable-linger "$EXPECTED_USER"
sudo install -o root -g root -m 0644 "$RUNTIME_UNIT_SOURCE" "$RUNTIME_UNIT_TARGET"
sudo install -d -o root -g root -m 0755 /etc/systemd/system/obs.service.d
sudo install -o root -g root -m 0644 "$OBS_DROPIN_SOURCE" "$OBS_DROPIN_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$RUNTIME_UNIT" >/dev/null

MUTATION_STARTED=0
restore_previous_runtime() {
	local rc="${1:-1}"
	trap - ERR INT TERM
	set +e
	echo "runtime migration failed; restoring the previous supervisor path" >&2
	if [ "$MUTATION_STARTED" -eq 1 ]; then
		sudo systemctl stop "$RUNTIME_UNIT" >/dev/null 2>&1
		if [ "$RUNTIME_WAS_ACTIVE" -eq 1 ]; then
			sudo systemctl start "$RUNTIME_UNIT" >/dev/null 2>&1
		else
			sudo systemctl disable "$RUNTIME_UNIT" >/dev/null 2>&1
			rm -f tmp/stop
			./start_all.sh --daemon >/dev/null 2>&1
		fi
	fi
	if [ "$LINGER_WAS_ENABLED" -eq 0 ]; then
		sudo loginctl disable-linger "$EXPECTED_USER" >/dev/null 2>&1
	fi
	exit "$rc"
}

wait_pid_down() {
	local pid="$1" owner state deadline=$(( $(date +%s) + 30 ))
	while true; do
		owner=$(cat tmp/state/start_all.pid 2>/dev/null || true)
		# start_all removes its ownership pidfile after worker cleanup. A shell
		# may remain briefly visible as a zombie under tmux; kill -0 alone would
		# incorrectly wait on that already-finished supervisor for 30 seconds.
		[ "$owner" = "$pid" ] || return 0
		kill -0 "$pid" 2>/dev/null || return 0
		state=$(ps -p "$pid" -o stat= 2>/dev/null || true)
		case "$state" in Z*|*Z*) return 0 ;; esac
		[ "$(date +%s)" -lt "$deadline" ] || return 1
		sleep 1
	done
}

cdp_port_from_endpoint() {
	python3 - "$SCRIPT_DIR/tmp/cdp_endpoint.json" <<'PY' 2>/dev/null
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    endpoint = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = endpoint.get("url")
    pid = endpoint.get("pid")
    if not isinstance(url, str) or not url:
        raise ValueError("missing URL")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("CDP endpoint is not local HTTP")
    port = endpoint.get("port", parsed.port)
    if isinstance(port, bool) or isinstance(pid, bool):
        raise ValueError("boolean endpoint metadata")
    port = int(port)
    pid = int(pid)
    if parsed.port is not None and parsed.port != port:
        raise ValueError("URL and port disagree")
    if not 1 <= port <= 65535 or pid <= 0:
        raise ValueError("endpoint metadata out of range")
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)

print(port)
PY
}

old_pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
MUTATION_STARTED=1
trap 'restore_previous_runtime $?' ERR
trap 'restore_previous_runtime 130' INT TERM

if [ "$RUNTIME_WAS_ACTIVE" -eq 1 ]; then
	sudo systemctl stop "$RUNTIME_UNIT"
else
	./stop_soren.sh >/dev/null
fi
case "$old_pid" in
''|*[!0-9]*) ;;
*) wait_pid_down "$old_pid" ;;
esac

sudo systemctl start "$RUNTIME_UNIT"

verified=0
for _attempt in $(seq 1 45); do
	main_pid=$(systemctl show "$RUNTIME_UNIT" -p MainPID --value 2>/dev/null || true)
	pidfile_pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
	loop_pid=$(cat tmp/.soren_loop.lock/pid 2>/dev/null || true)
	audio_pid=$(cat tmp/state/audio_worker.pid 2>/dev/null || true)
	watchdog_pid=$(cat tmp/state/.soviet_watchdog.lock/owner 2>/dev/null || true)
	serve_pid=$(lsof -nP -iTCP:8080 -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
	cdp_port=$(cdp_port_from_endpoint || true)
	cdp_pid=""
	if [ -n "$cdp_port" ]; then
		cdp_pid=$(lsof -nP -iTCP:"$cdp_port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
	fi
	game_state_mtime=$(stat -c %Y game_state.json 2>/dev/null || echo 0)
	render_mtime=$(stat -c %Y tmp/state/game_render_health.json 2>/dev/null || echo 0)
	now=$(date +%s)
	default_sink=$(PULSE_SERVER=unix:/run/user/1001/pulse/native pactl get-default-sink 2>/dev/null || true)
	default_source=$(PULSE_SERVER=unix:/run/user/1001/pulse/native pactl get-default-source 2>/dev/null || true)
	if systemctl is-active --quiet "$RUNTIME_UNIT" \
		&& [ "$main_pid" = "$pidfile_pid" ] \
		&& kill -0 "$main_pid" 2>/dev/null \
		&& kill -0 "$loop_pid" 2>/dev/null \
		&& kill -0 "$audio_pid" 2>/dev/null \
		&& kill -0 "$watchdog_pid" 2>/dev/null \
		&& [ -n "$serve_pid" ] \
		&& [ -n "$cdp_pid" ] \
		&& [ "$game_state_mtime" -gt 0 ] \
		&& [ "$render_mtime" -gt 0 ] \
		&& [ "$((now - game_state_mtime))" -lt 30 ] \
		&& [ "$((now - render_mtime))" -lt 30 ] \
		&& [ "$default_sink" = soren_null ] \
		&& [ "$default_source" = soren_null.monitor ]; then
		verified=1
		break
	fi
	sleep 1
done
[ "$verified" -eq 1 ] || {
	echo "runtime service did not become healthy before the deadline" >&2
	false
}

if [ "$OBS_WAS_LIVE" -eq 1 ]; then
	if ! systemctl is-active --quiet obs.service \
		|| [ "$(./obs_control.sh stream-status 2>/dev/null || true)" != "streaming=on" ]; then
		echo "OBS continuity was lost during runtime migration" >&2
		false
	fi
fi

trap - ERR INT TERM
printf 'runtime_migration=ok backend=%s main_pid=%s obs_continuity=%s linger=enabled\n' \
	"$(backend_name)" "$main_pid" "$OBS_WAS_LIVE"
status
