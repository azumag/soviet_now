#!/bin/bash
# Controlled OBS -> FFmpeg live cutover with automatic rollback.
# Destination credentials stay in /etc/soren-rtmp/push.conf and are never read
# into this process except for counting configured push directives.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
OBS_UNIT="${OBS_SYSTEMD_UNIT:-obs.service}"
RELAY_UNIT="soren-rtmp-relay.service"
RELAY_CONFIG="/etc/soren-rtmp/nginx.conf"
PUSH_CONFIG="/etc/soren-rtmp/push.conf"
VERIFY_SEC="${SOREN_DIRECT_CUTOVER_VERIFY_SEC:-20}"
MIN_FPS="${SOREN_DIRECT_CUTOVER_MIN_FPS:-29.0}"
MIN_SPEED="${SOREN_DIRECT_CUTOVER_MIN_SPEED:-0.97}"
MAX_DROP_DELTA="${SOREN_DIRECT_CUTOVER_MAX_DROP_DELTA:-2}"
MAX_DUP_DELTA="${SOREN_DIRECT_CUTOVER_MAX_DUP_DELTA:-4}"

usage() {
	cat <<'EOF'
Usage:
  ./cutover_direct_stream.sh --print-plan
  ./cutover_direct_stream.sh --preflight
  ./cutover_direct_stream.sh --cutover --confirm-live-cutover
  ./cutover_direct_stream.sh --rollback --confirm-live-rollback

The cutover stops the Soren supervisor and OBS stream/service, switches only
SOREN_STREAM_BACKEND in .env, starts the supervisor with FFmpeg, and verifies
output progress. Any post-mutation failure restores .env and the OBS stream.
EOF
}

case "${1:-}" in
--print-plan)
	printf '%s\n' \
		'1. Validate Linux, OBS live state, loopback relay, X11, PulseAudio, and at least one private push destination.' \
		'2. Reload the syntax-validated relay so the current private push destinations are active.' \
		'3. Back up .env without displaying it.' \
		'4. Stop the Soren supervisor and OBS, then set SOREN_STREAM_BACKEND=ffmpeg.' \
		'5. Start the supervisor and verify FFmpeg fps/speed/drop/dup progress.' \
		'6. On any failure, restore .env, OBS streaming, and the supervisor.' \
		'7. A confirmed rollback restores the recorded OBS .env backup and measures the recovery time.'
	exit 0
	;;
--preflight) ACTION=preflight ;;
--cutover)
	if [ "${2:-}" != "--confirm-live-cutover" ] || [ -n "${3:-}" ]; then
		echo "Live cutover requires: --cutover --confirm-live-cutover" >&2
		exit 2
	fi
	ACTION=cutover
	;;
--rollback)
	if [ "${2:-}" != "--confirm-live-rollback" ] || [ -n "${3:-}" ]; then
		echo "Live rollback requires: --rollback --confirm-live-rollback" >&2
		exit 2
	fi
	ACTION=rollback
	;;
--help|-h) usage; exit 0 ;;
*) usage >&2; exit 2 ;;
esac

die() {
	echo "cutover: $*" >&2
	exit 2
}

if [ "$(uname -s)" != "Linux" ]; then
	die "Linux only"
fi
if [ ! -f "$ENV_FILE" ]; then
	die "missing environment file: $ENV_FILE"
fi
for file in direct_stream.sh start_all.sh stop_soren.sh obs_control.sh lib/direct_stream.py; do
	if [ ! -f "$file" ]; then
		die "missing required file: $file"
	fi
done
case "$VERIFY_SEC:$MAX_DROP_DELTA:$MAX_DUP_DELTA" in
*[!0-9:]*|:*|*::*|*:) die "verification seconds and frame deltas must be non-negative integers" ;;
esac
if [ "$VERIFY_SEC" -lt 10 ] || [ "$VERIFY_SEC" -gt 120 ]; then
	die "SOREN_DIRECT_CUTOVER_VERIFY_SEC must be between 10 and 120"
fi
python3 - "$MIN_FPS" "$MIN_SPEED" <<'PY' >/dev/null || die "fps/speed thresholds are invalid"
import sys
fps, speed = map(float, sys.argv[1:])
raise SystemExit(0 if 1 <= fps <= 60 and 0.1 <= speed <= 2 else 1)
PY

read_backend() (
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
	printf '%s\n' "${SOREN_STREAM_BACKEND:-obs}"
)

push_destination_count() {
	local count
	count=$(sudo -n awk '/^[[:space:]]*push[[:space:]]/{n++} END{print n+0}' "$PUSH_CONFIG") \
		|| die "cannot count private relay destinations with passwordless sudo"
	case "$count" in ''|*[!0-9]*) die "invalid push destination count" ;; esac
	printf '%s\n' "$count"
}

validate_direct_live() (
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
	export SOREN_STREAM_BACKEND=ffmpeg
	exec python3 "$SCRIPT_DIR/lib/direct_stream.py" validate --mode live
)

SUPERVISOR_UNIT=""
SUPERVISOR_SCOPE="daemon"
detect_supervisor_unit() {
	local unit
	if systemctl is-active --quiet soren-runtime.service 2>/dev/null; then
		SUPERVISOR_UNIT="soren-runtime.service"
		SUPERVISOR_SCOPE="system"
		return 0
	fi
	for unit in start-all.service soren.service soren-supervisor.service; do
		if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
			SUPERVISOR_UNIT="$unit"
			SUPERVISOR_SCOPE="user"
			return 0
		fi
	done
	SUPERVISOR_UNIT=""
	SUPERVISOR_SCOPE="daemon"
}

PUSH_COUNT=0
OBS_WAS_LIVE=0
preflight() {
	local backend obs_state supervisor_pid
	backend=$(read_backend)
	if [ "$backend" != "obs" ]; then
		die "expected SOREN_STREAM_BACKEND=obs before cutover (found $backend)"
	fi
	if ! sudo -n true >/dev/null 2>&1; then
		die "passwordless sudo is required for the controlled service cutover"
	fi
	if ! systemctl is-active --quiet "$OBS_UNIT"; then
		die "$OBS_UNIT is not active"
	fi
	obs_state=$(./obs_control.sh stream-status 2>/dev/null || true)
	if [ "$obs_state" != "streaming=on" ]; then
		die "OBS must be streaming before the continuity cutover"
	fi
	OBS_WAS_LIVE=1
	if ! systemctl is-active --quiet "$RELAY_UNIT"; then
		die "$RELAY_UNIT is not active"
	fi
	PUSH_COUNT=$(push_destination_count)
	if [ "$PUSH_COUNT" -lt 1 ]; then
		die "no private relay push destination is configured; use sudoedit on $PUSH_CONFIG"
	fi
	sudo -n nginx -t -q -c "$RELAY_CONFIG"
	validate_direct_live >/dev/null
	supervisor_pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
	case "$supervisor_pid" in ''|*[!0-9]*) die "Soren supervisor pidfile is missing or invalid" ;; esac
	if ! kill -0 "$supervisor_pid" 2>/dev/null; then
		die "Soren supervisor is not running"
	fi
	detect_supervisor_unit
	printf 'preflight=ok backend=obs obs_streaming=on relay=active push_destinations=%s supervisor=%s\n' \
		"$PUSH_COUNT" "${SUPERVISOR_SCOPE}:${SUPERVISOR_UNIT:-start_all}"
}

if [ "$ACTION" = "preflight" ]; then
	preflight
	exit 0
fi

set_env_backend() {
	local value="$1" temporary
	temporary=$(mktemp "$SCRIPT_DIR/tmp/.env.cutover.XXXXXX")
	awk -v replacement="SOREN_STREAM_BACKEND='$value'" '
		BEGIN { replaced = 0 }
		/^[[:space:]]*SOREN_STREAM_BACKEND=/ {
			if (!replaced) print replacement
			replaced = 1
			next
		}
		{ print }
		END { if (!replaced) print replacement }
	' "$ENV_FILE" >"$temporary"
	chmod 600 "$temporary"
	mv "$temporary" "$ENV_FILE"
}

wait_pid_down() {
	local pid="$1" owner state deadline=$(( $(date +%s) + 20 ))
	while true; do
		owner=$(cat tmp/state/start_all.pid 2>/dev/null || true)
		[ "$owner" = "$pid" ] || return 0
		kill -0 "$pid" 2>/dev/null || return 0
		state=$(ps -p "$pid" -o stat= 2>/dev/null || true)
		case "$state" in Z*|*Z*) return 0 ;; esac
		if [ "$(date +%s)" -ge "$deadline" ]; then
			return 1
		fi
		sleep 1
	done
}

stop_supervisor() {
	local old_pid
	old_pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
	if [ "$SUPERVISOR_SCOPE" = "system" ]; then
		sudo -n systemctl stop "$SUPERVISOR_UNIT"
	elif [ "$SUPERVISOR_SCOPE" = "user" ]; then
		systemctl --user stop "$SUPERVISOR_UNIT"
	else
		./stop_soren.sh >/dev/null
	fi
	case "$old_pid" in ''|*[!0-9]*) return 0 ;; esac
	wait_pid_down "$old_pid"
}

start_supervisor() {
	if [ "$SUPERVISOR_SCOPE" = "system" ]; then
		sudo -n systemctl start "$SUPERVISOR_UNIT"
	elif [ "$SUPERVISOR_SCOPE" = "user" ]; then
		systemctl --user start "$SUPERVISOR_UNIT"
	else
		./start_all.sh --daemon
	fi
}

wait_obs_websocket() {
	local attempt
	for attempt in $(seq 1 30); do
		if ./obs_control.sh stream-status >/dev/null 2>&1; then
			return 0
		fi
		sleep 1
	done
	return 1
}

wait_direct_running() {
	local started_after="$1" attempt status
	for attempt in $(seq 1 30); do
		status=$(./direct_stream.sh status 2>/dev/null || true)
		if python3 - "$status" "$started_after" <<'PY' >/dev/null 2>&1
import json
import sys
try:
    state = json.loads(sys.argv[1])
    good = state.get("running") is True and int(state.get("started_at", 0)) >= int(sys.argv[2])
except Exception:
    good = False
raise SystemExit(0 if good else 1)
PY
		then
			printf '%s\n' "$status"
			return 0
		fi
		sleep 1
	done
	return 1
}

verify_progress() {
	local before="$1" after="$2"
	python3 - "$before" "$after" "$VERIFY_SEC" "$MIN_FPS" "$MIN_SPEED" "$MAX_DROP_DELTA" "$MAX_DUP_DELTA" <<'PY'
import json
import sys

before, after = map(json.loads, sys.argv[1:3])
seconds = int(sys.argv[3])
min_fps = float(sys.argv[4])
min_speed = float(sys.argv[5])
max_drop = int(sys.argv[6])
max_dup = int(sys.argv[7])

frame_delta = int(after.get("frame", 0)) - int(before.get("frame", 0))
drop_delta = int(after.get("drop_frames", 0)) - int(before.get("drop_frames", 0))
dup_delta = int(after.get("dup_frames", 0)) - int(before.get("dup_frames", 0))
fps = float(after.get("fps", 0))
speed = float(after.get("speed", 0))
effective_fps = frame_delta / seconds
checks = {
    "running": after.get("running") is True,
    "fps": fps >= min_fps,
    "speed": speed >= min_speed,
    "frame_progress": effective_fps >= min_fps - 1,
    "drop_delta": 0 <= drop_delta <= max_drop,
    "dup_delta": 0 <= dup_delta <= max_dup,
}
result = {
    "ok": all(checks.values()),
    "checks": checks,
    "fps": fps,
    "speed": speed,
    "effective_fps": round(effective_fps, 3),
    "drop_delta": drop_delta,
    "dup_delta": dup_delta,
    "verify_seconds": seconds,
}
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["ok"] else 1)
PY
}

BACKUP=""
MUTATION_STARTED=0
rollback() {
	local original_rc="${1:-1}"
	trap - ERR INT TERM
	set +e
	echo "cutover: verification failed; restoring OBS path" >&2
	if [ "$MUTATION_STARTED" -eq 1 ]; then
		stop_supervisor >/dev/null 2>&1
		SOREN_ENV_FILE="$ENV_FILE" ./direct_stream.sh stop >/dev/null 2>&1
		if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
			cp -p "$BACKUP" "$ENV_FILE"
		fi
		sudo -n systemctl start "$OBS_UNIT"
		if wait_obs_websocket && [ "$OBS_WAS_LIVE" -eq 1 ]; then
			./obs_control.sh stream-start >/dev/null 2>&1
		fi
		start_supervisor >/dev/null 2>&1
	fi
	exit "$original_rc"
}

recorded_cutover_backup() {
	python3 - "$ENV_FILE" "tmp/state/direct_cutover.json" <<'PY'
import json
from pathlib import Path
import sys

env_file = Path(sys.argv[1]).resolve()
state_file = Path(sys.argv[2])
try:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    backup = Path(str(state.get("env_backup") or "")).resolve(strict=True)
except Exception:
    raise SystemExit(1)
valid = (
    backup.is_file()
    and backup.parent == env_file.parent
    and backup.name.startswith(env_file.name + ".direct-cutover-backup.")
)
if not valid:
    raise SystemExit(1)
print(backup)
PY
}

backend_from_file() {
	python3 - "$1" <<'PY'
from pathlib import Path
import re
import sys

backend = "obs"
try:
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
except Exception:
    raise SystemExit(1)
for line in lines:
    match = re.match(r"^[ \t]*SOREN_STREAM_BACKEND[ \t]*=[ \t]*(.*?)[ \t]*$", line)
    if match:
        backend = match.group(1).strip().strip("'\"").lower()
        break
print(backend)
PY
}

ROLLBACK_SAFETY_BACKUP=""
ROLLBACK_MUTATION_STARTED=0
restore_direct_after_rollback_failure() {
	local original_rc="${1:-1}"
	trap - ERR INT TERM
	set +e
	echo "cutover: OBS rollback failed; restoring the FFmpeg path" >&2
	if [ "$ROLLBACK_MUTATION_STARTED" -eq 1 ]; then
		stop_supervisor >/dev/null 2>&1
		./obs_control.sh stream-stop >/dev/null 2>&1
		sudo -n systemctl stop "$OBS_UNIT" >/dev/null 2>&1
		if [ -n "$ROLLBACK_SAFETY_BACKUP" ] && [ -f "$ROLLBACK_SAFETY_BACKUP" ]; then
			cp -p "$ROLLBACK_SAFETY_BACKUP" "$ENV_FILE"
		fi
		start_supervisor >/dev/null 2>&1
	fi
	exit "$original_rc"
}

manual_rollback() {
	local backend supervisor_pid obs_backup obs_backup_backend rollback_started rollback_elapsed direct_status
	backend=$(read_backend)
	if [ "$backend" != "ffmpeg" ]; then
		die "expected SOREN_STREAM_BACKEND=ffmpeg before rollback (found $backend)"
	fi
	if ! sudo -n true >/dev/null 2>&1; then
		die "passwordless sudo is required for the controlled service rollback"
	fi
	if ! systemctl is-active --quiet "$RELAY_UNIT"; then
		die "$RELAY_UNIT is not active"
	fi
	obs_backup=$(recorded_cutover_backup) || die "recorded OBS .env backup is missing or outside the allowed path"
	obs_backup_backend=$(backend_from_file "$obs_backup") || die "cannot validate the recorded OBS .env backup"
	if [ "$obs_backup_backend" != "obs" ]; then
		die "recorded rollback backup does not select the OBS backend"
	fi
	supervisor_pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
	case "$supervisor_pid" in ''|*[!0-9]*) die "Soren supervisor pidfile is missing or invalid" ;; esac
	if ! kill -0 "$supervisor_pid" 2>/dev/null; then
		die "Soren supervisor is not running"
	fi
	detect_supervisor_unit
	mkdir -p tmp/state
	ROLLBACK_SAFETY_BACKUP="${ENV_FILE}.direct-rollback-safety.$(date '+%Y%m%d-%H%M%S')"
	cp -p "$ENV_FILE" "$ROLLBACK_SAFETY_BACKUP"
	chmod 600 "$ROLLBACK_SAFETY_BACKUP" 2>/dev/null || true
	ROLLBACK_MUTATION_STARTED=1
	trap 'restore_direct_after_rollback_failure $?' ERR
	trap 'restore_direct_after_rollback_failure 130' INT TERM

	rollback_started=$(date +%s)
	stop_supervisor
	SOREN_ENV_FILE="$ENV_FILE" ./direct_stream.sh stop >/dev/null 2>&1 || true
	cp -p "$obs_backup" "$ENV_FILE"
	sudo -n systemctl start "$OBS_UNIT"
	wait_obs_websocket
	./obs_control.sh stream-start >/dev/null
	start_supervisor >/dev/null

	if [ "$(read_backend)" != "obs" ]; then
		echo "cutover: backend did not return to OBS" >&2
		restore_direct_after_rollback_failure 1
	fi
	if ! systemctl is-active --quiet "$OBS_UNIT"; then
		echo "cutover: $OBS_UNIT is not active after rollback" >&2
		restore_direct_after_rollback_failure 1
	fi
	if [ "$(./obs_control.sh stream-status 2>/dev/null || true)" != "streaming=on" ]; then
		echo "cutover: OBS stream did not resume" >&2
		restore_direct_after_rollback_failure 1
	fi
	direct_status=$(SOREN_ENV_FILE="$ENV_FILE" ./direct_stream.sh status 2>/dev/null || true)
	if ! python3 - "$direct_status" <<'PY' >/dev/null 2>&1
import json
import sys
try:
    running = json.loads(sys.argv[1]).get("running") is True
except Exception:
    running = True
raise SystemExit(1 if running else 0)
PY
	then
		echo "cutover: direct publisher remained active after OBS rollback" >&2
		restore_direct_after_rollback_failure 1
	fi

	rollback_elapsed=$(( $(date +%s) - rollback_started ))
	trap - ERR INT TERM
	python3 - "tmp/state/direct_cutover.json" "$rollback_elapsed" <<'PY'
import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
try:
    state = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    state = {}
state.update({
    "state": "obs_rolled_back",
    "rolled_back_at": int(time.time()),
    "rollback_elapsed_sec": int(sys.argv[2]),
    "rollback_within_60_sec": int(sys.argv[2]) <= 60,
})
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
print(json.dumps({
    "state": state["state"],
    "rollback_elapsed_sec": state["rollback_elapsed_sec"],
    "rollback_within_60_sec": state["rollback_within_60_sec"],
}, sort_keys=True))
PY
	if [ "$rollback_elapsed" -gt 60 ]; then
		echo "cutover: OBS recovered but exceeded the 60-second acceptance limit" >&2
		return 1
	fi
	return 0
}

if [ "$ACTION" = "rollback" ]; then
	manual_rollback
	exit $?
fi

preflight
# nginx reads push.conf only during start/reload. Activate the exact config
# that preflight just syntax-validated before interrupting OBS. A failed reload
# leaves the current relay process and OBS path untouched.
if ! sudo -n systemctl reload "$RELAY_UNIT"; then
	die "failed to reload the validated relay configuration"
fi
if ! systemctl is-active --quiet "$RELAY_UNIT"; then
	die "$RELAY_UNIT is not active after configuration reload"
fi
mkdir -p tmp/state
BACKUP="${ENV_FILE}.direct-cutover-backup.$(date '+%Y%m%d-%H%M%S')"
cp -p "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP" 2>/dev/null || true
MUTATION_STARTED=1
trap 'rollback $?' ERR
trap 'rollback 130' INT TERM

CUTOVER_STARTED=$(date +%s)
stop_supervisor
./obs_control.sh stream-stop >/dev/null
sudo -n systemctl stop "$OBS_UNIT"
set_env_backend ffmpeg
start_supervisor >/dev/null

if ! STATUS_BEFORE=$(wait_direct_running "$CUTOVER_STARTED"); then
	echo "cutover: FFmpeg runner did not become healthy within 30 seconds" >&2
	rollback 1
fi
sleep "$VERIFY_SEC"
STATUS_AFTER=$(./direct_stream.sh status)
if ! QUALITY=$(verify_progress "$STATUS_BEFORE" "$STATUS_AFTER"); then
	printf 'cutover: quality gate failed: %s\n' "$QUALITY" >&2
	rollback 1
fi
if systemctl is-active --quiet "$OBS_UNIT"; then
	echo "cutover: $OBS_UNIT unexpectedly remained active" >&2
	rollback 1
fi
if ! systemctl is-active --quiet "$RELAY_UNIT"; then
	echo "cutover: $RELAY_UNIT stopped during verification" >&2
	rollback 1
fi

trap - ERR INT TERM
python3 - "$QUALITY" "$PUSH_COUNT" "$BACKUP" <<'PY'
import json
import sys
from pathlib import Path
import time

quality = json.loads(sys.argv[1])
state = {
    "state": "ffmpeg_live",
    "verified_at": int(time.time()),
    "push_destination_count": int(sys.argv[2]),
    "quality": quality,
    "env_backup": sys.argv[3],
}
target = Path("tmp/state/direct_cutover.json")
temporary = target.with_suffix(".json.tmp")
temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(target)
print(json.dumps(state, sort_keys=True))
PY
