#!/bin/bash
# Destructive-but-bounded recovery acceptance for the FFmpeg live path.
# Restarts only the loopback RTMP relay, then requires the supervisor to restore
# one FFmpeg process and one relay input. Any failure attempts the recorded OBS
# rollback created by cutover_direct_stream.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
OBS_UNIT="${OBS_SYSTEMD_UNIT:-obs.service}"
RELAY_UNIT="soren-rtmp-relay.service"
TIMEOUT_SEC="${SOREN_DIRECT_RECOVERY_TIMEOUT_SEC:-60}"
STATE_FILE="${SOREN_DIRECT_RECOVERY_STATE_FILE:-tmp/state/direct_recovery_test.json}"

usage() {
	cat <<'EOF' >&2
Usage:
  ./direct_stream_recovery_test.sh --print-plan
  ./direct_stream_recovery_test.sh --run --confirm-live-recovery-test

The run briefly interrupts the active FFmpeg path by restarting only the local
RTMP relay. It verifies automatic FFmpeg restart, one publisher process, one
relay input connection, resumed frame progress, and OBS remaining inactive.
On failure it attempts the recorded OBS rollback.
EOF
	exit 2
}

case "${1:-}" in
--print-plan)
	printf '%s\n' \
		'1. Require an already verified FFmpeg live cutover and inactive OBS.' \
		'2. Restart only the loopback RTMP relay.' \
		'3. Require a new FFmpeg run, one process, one relay input, and frame progress within 60 seconds.' \
		'4. Automatically restore the recorded OBS path on failure.'
	exit 0
	;;
--run)
	[ "${2:-}" = "--confirm-live-recovery-test" ] && [ -z "${3:-}" ] || usage
	;;
*) usage ;;
esac

case "$TIMEOUT_SEC" in
''|*[!0-9]*) echo "recovery timeout must be an integer" >&2; exit 2 ;;
esac
if [ "$TIMEOUT_SEC" -lt 20 ] || [ "$TIMEOUT_SEC" -gt 180 ]; then
	echo "recovery timeout must be between 20 and 180 seconds" >&2
	exit 2
fi
if [ "$(uname -s)" != "Linux" ]; then
	echo "recovery test is Linux-only" >&2
	exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
	echo "missing environment file: $ENV_FILE" >&2
	exit 2
fi
for command_name in python3 sudo systemctl ss ps; do
	command -v "$command_name" >/dev/null 2>&1 || {
		echo "required command not found: $command_name" >&2
		exit 2
	}
done
for file in direct_stream.sh cutover_direct_stream.sh lib/direct_soak.py; do
	[ -f "$file" ] || {
		echo "required file not found: $file" >&2
		exit 2
	}
done
sudo -n true

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
if [ "${SOREN_STREAM_BACKEND:-obs}" != "ffmpeg" ]; then
	echo "recovery test requires SOREN_STREAM_BACKEND=ffmpeg" >&2
	exit 2
fi
if systemctl is-active --quiet "$OBS_UNIT"; then
	echo "OBS must be inactive during direct recovery testing" >&2
	exit 2
fi
if ! systemctl is-active --quiet "$RELAY_UNIT"; then
	echo "RTMP relay is not active" >&2
	exit 2
fi

direct_status() {
	SOREN_ENV_FILE="$ENV_FILE" ./direct_stream.sh status
}

publisher_shape() {
	python3 - <<'PY'
from lib.direct_soak import _publisher_count, _relay_publisher_connection_count
print(f"{_publisher_count()}:{_relay_publisher_connection_count()}")
PY
}

BEFORE_STATUS=$(direct_status)
BEFORE_STARTED=$(python3 - "$BEFORE_STATUS" <<'PY'
import json
import sys
try:
    value = json.loads(sys.argv[1])
    good = value.get("running") is True
    started = int(value.get("started_at", 0))
except Exception:
    good = False
    started = 0
print(started)
raise SystemExit(0 if good and started > 0 else 1)
PY
) || {
	echo "direct stream is not running before recovery test" >&2
	exit 2
}
if [ "$(publisher_shape)" != "1:1" ]; then
	echo "recovery test requires exactly one publisher process and relay input" >&2
	exit 2
fi

mkdir -p "$(dirname "$STATE_FILE")"
ROLLBACK_ARMED=1
recover_obs_on_failure() {
	local original_rc="${1:-1}"
	trap - ERR INT TERM
	set +e
	echo "direct_recovery: failed; attempting recorded OBS rollback" >&2
	if [ "$ROLLBACK_ARMED" -eq 1 ]; then
		SOREN_ENV_FILE="$ENV_FILE" ./cutover_direct_stream.sh --rollback --confirm-live-rollback >&2
	fi
	exit "$original_rc"
}
trap 'recover_obs_on_failure $?' ERR
trap 'recover_obs_on_failure 130' INT TERM

TEST_STARTED=$(date +%s)
sudo -n systemctl restart "$RELAY_UNIT"
DEADLINE=$(( TEST_STARTED + TIMEOUT_SEC ))
AFTER_STATUS=""
while [ "$(date +%s)" -le "$DEADLINE" ]; do
	if systemctl is-active --quiet "$RELAY_UNIT" && ! systemctl is-active --quiet "$OBS_UNIT"; then
		candidate=$(direct_status 2>/dev/null || true)
		if python3 - "$candidate" "$BEFORE_STARTED" <<'PY' >/dev/null 2>&1
import json
import sys
try:
    value = json.loads(sys.argv[1])
    recovered = (
        value.get("running") is True
        and int(value.get("started_at", 0)) != int(sys.argv[2])
        and int(value.get("frame", 0)) > 0
        and float(value.get("fps", 0)) >= 29.0
        and float(value.get("speed", 0)) >= 0.97
    )
except Exception:
    recovered = False
raise SystemExit(0 if recovered else 1)
PY
		then
			if [ "$(publisher_shape)" = "1:1" ]; then
				AFTER_STATUS="$candidate"
				break
			fi
		fi
	fi
	sleep 1
done
if [ -z "$AFTER_STATUS" ]; then
	echo "direct stream did not recover within ${TIMEOUT_SEC}s" >&2
	false
fi

RECOVERY_SEC=$(( $(date +%s) - TEST_STARTED ))
python3 - "$STATE_FILE" "$BEFORE_STATUS" "$AFTER_STATUS" "$RECOVERY_SEC" <<'PY'
import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
before = json.loads(sys.argv[2])
after = json.loads(sys.argv[3])
payload = {
    "state": "passed",
    "tested_at": int(time.time()),
    "recovery_sec": int(sys.argv[4]),
    "within_60_sec": int(sys.argv[4]) <= 60,
    "before_started_at": before.get("started_at"),
    "after_started_at": after.get("started_at"),
    "after_fps": after.get("fps"),
    "after_speed": after.get("speed"),
    "publisher_process_count": 1,
    "relay_publisher_connection_count": 1,
    "obs_active": False,
}
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
print(json.dumps(payload, sort_keys=True))
PY

ROLLBACK_ARMED=0
trap - ERR INT TERM
