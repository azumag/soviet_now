#!/bin/bash
# workers/deadline_monitor.sh - deadline misplacement monitor worker

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

WORKER_NAME="deadline_monitor"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
LOG_FILE="logs/${WORKER_NAME}.log"

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_pid_alive() {
	local pid="${1:-}" err=""
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in
	*"operation not permitted"*|*"Operation not permitted"*) return 0 ;;
	esac
	return 1
}

_cleanup() {
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ "$active_pid" != "$$" ]; then
		_log "cleanup skipped: pidfile owner is ${active_pid:-none} (self=$$)"
		return 0
	fi
	if [ "$active_pid" = "$$" ]; then
		rm -f "$PID_FILE"
	fi
	_log "停止"
}

trap '_cleanup; trap - EXIT; exit 130' INT TERM
trap '_cleanup' EXIT

mkdir -p tmp/state logs
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if _pid_alive "$old_pid"; then
		_log "already running (PID=$old_pid) -> no-op"
		trap - EXIT
		exit 0
	fi
	rm -f "$PID_FILE"
fi
echo $$ >"$PID_FILE"

_log "起動 (PID=$$)" >>"$LOG_FILE"
exec python3 deadline_misplacement_monitor.py >>"$LOG_FILE" 2>&1
