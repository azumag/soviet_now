#!/bin/bash
# tools/poll_status.sh - bounded, sleep-backed status probe (docich#2688)
#
# Ad-hoc monitoring used to be written as:
#
#   while true; do
#     python3 -c 'import json; print(json.load(open("tmp/state/current_prediction.json"))...)'
#     python3 extract_decide_hash.py strategy.py
#   done
#
# with no sleep. Each python3 exits in milliseconds, so a tight loop spawns
# them back-to-back and pins a core; if the parent shell is killed the children
# can be observed as orphans at ~85% CPU (2026-10-23 23:03 JST). This tool is
# the canonical replacement: one bounded probe per tick, one python3 process
# for both reads, and a validated wait of at least one second between ticks.
#
# Usage:
#   tools/poll_status.sh [--once]
#   tools/poll_status.sh [--interval SEC] [--count N | --duration SEC]
#
# Defaults: interval=2, duration=60 when neither --count nor --duration is
# given. --duration is capped at 3600s so a mistyped invocation cannot leave a
# permanent poller behind.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# shellcheck source=/dev/null
source ./lib/poll_wait.sh 2>/dev/null || docich_poll_sleep() { sleep "${1:-1}" 2>/dev/null || true; }

usage() {
	cat >&2 <<'EOF'
usage: tools/poll_status.sh [--once]
       tools/poll_status.sh [--interval SEC] [--count N | --duration SEC]
EOF
}

interval_raw=""
count=0
duration=0
once=0
while [ "$#" -gt 0 ]; do
	case "$1" in
	--once) once=1 ;;
	--interval)
		shift
		interval_raw="${1:-}"
		;;
	--interval=*) interval_raw="${1#*=}" ;;
	--count)
		shift
		count="${1:-0}"
		;;
	--count=*) count="${1#*=}" ;;
	--duration)
		shift
		duration="${1:-0}"
		;;
	--duration=*) duration="${1#*=}" ;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		usage
		exit 2
		;;
	esac
	shift
done

case "$count" in '' | *[!0-9]*) count=0 ;; esac
case "$duration" in '' | *[!0-9]*) duration=0 ;; esac
INTERVAL=$(docich_poll_interval "$interval_raw" 2 1 2>/dev/null || printf '2')
if [ "$once" -eq 0 ] && [ "$count" -eq 0 ] && [ "$duration" -eq 0 ]; then
	duration=60
fi
[ "$duration" -le 3600 ] || duration=3600

TMP_STATE_DIR="${TMP_STATE_DIR:-tmp/state}"
STRATEGY_FILE="${STRATEGY_FILE:-strategy.py}"
PYTHON="${PYTHON:-python3}"

probe_once() {
	# Both values come from one interpreter: spawning one python3 per field is
	# what made the old ad-hoc probe expensive even before the missing sleep.
	"$PYTHON" - "$TMP_STATE_DIR/current_prediction.json" "$STRATEGY_FILE" <<'PY' 2>/dev/null
import json
import sys

state_path, strategy_path = sys.argv[1:3]
try:
    with open(state_path, encoding="utf-8") as stream:
        state = json.load(stream)
except Exception:
    state = {}
try:
    from extract_decide_hash import compute_hash
    decide_hash = compute_hash(strategy_path)
except Exception:
    decide_hash = ""
print(f"round_version={state.get('round_version', 0)} decide_hash={decide_hash}")
PY
}

started=$(date +%s)
ticks=0
while :; do
	probe_once
	ticks=$((ticks + 1))
	[ "$once" -eq 1 ] && break
	[ "$count" -gt 0 ] && [ "$ticks" -ge "$count" ] && break
	if [ "$duration" -gt 0 ] && [ $(( $(date +%s) - started )) -ge "$duration" ]; then
		break
	fi
	sleep "$INTERVAL"
done
