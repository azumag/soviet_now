#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG_FILE="$SCRIPT_DIR/tmp/soren91.log"
STOP_FILE="$SCRIPT_DIR/tmp/stop"
PID_FILE="$SCRIPT_DIR/tmp/soren91.pid"
MAIN_PID_FILE="$SCRIPT_DIR/tmp/main.pid"
RUNNER_LOCK_DIR="$SCRIPT_DIR/tmp/.runner.lock"
RETRY_DELAY_SEC="${SOREN91_RESTART_DELAY_SEC:-3}"
RUNNER_LOCK_STALE_SEC="${SOREN91_RUNNER_LOCK_STALE_SEC:-120}"
CHILD_MAIN_PID=""

mkdir -p "$SCRIPT_DIR/tmp" 2>/dev/null || true

_pid_alive() {
	local pid="$1"
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	kill -0 "$pid" 2>/dev/null
}

_cleanup_lock() {
	local owner=""
	owner=$(sed -n 's/^pid=//p' "$RUNNER_LOCK_DIR/owner" 2>/dev/null | head -n 1)
	if [ "$owner" = "$$" ]; then
		rm -rf "$RUNNER_LOCK_DIR" 2>/dev/null || true
	fi
}

_acquire_runner_lock() {
	local now mt age owner=""
	while ! mkdir "$RUNNER_LOCK_DIR" 2>/dev/null; do
		owner=$(sed -n 's/^pid=//p' "$RUNNER_LOCK_DIR/owner" 2>/dev/null | head -n 1)
		if [ -n "$owner" ] && _pid_alive "$owner"; then
			printf '[%s] [runner] another runner is active (PID=%s); exit\n' "$(date '+%H:%M:%S')" "$owner" >>"$LOG_FILE" 2>/dev/null || true
			exit 0
		fi
		now=$(date +%s)
		mt=$(stat -f %m "$RUNNER_LOCK_DIR" 2>/dev/null || stat -c %Y "$RUNNER_LOCK_DIR" 2>/dev/null || echo "$now")
		age=$((now - mt))
		if [ "$age" -gt "$RUNNER_LOCK_STALE_SEC" ]; then
			rm -rf "$RUNNER_LOCK_DIR" 2>/dev/null || true
			continue
		fi
		sleep 1
	done
	{
		printf 'pid=%s\n' "$$"
		printf 'started_at=%s\n' "$(date '+%F %T')"
	} >"$RUNNER_LOCK_DIR/owner" 2>/dev/null || true
}

_stop_child_main() {
	local pid=""
	pid=$(cat "$MAIN_PID_FILE" 2>/dev/null || true)
	case "$pid" in ''|*[!0-9]*) pid="$CHILD_MAIN_PID" ;; esac
	if _pid_alive "$pid"; then
		kill "$pid" 2>/dev/null || true
		local waited=0
		while _pid_alive "$pid" && [ "$waited" -lt 20 ]; do
			sleep 0.1
			waited=$((waited + 1))
		done
		if _pid_alive "$pid"; then
			kill -9 "$pid" 2>/dev/null || true
		fi
	fi
	rm -f "$MAIN_PID_FILE" 2>/dev/null || true
}

_on_signal() {
	local sig="$1"
	printf '[%s] [runner] received %s; stopping child and exiting\n' "$(date '+%H:%M:%S')" "$sig" >>"$LOG_FILE" 2>/dev/null || true
	_stop_child_main
	_cleanup_lock
	exit 0
}

_on_exit() {
	local rc=$?
	printf '[%s] [runner] exit rc=%s\n' "$(date '+%H:%M:%S')" "$rc" >>"$LOG_FILE" 2>/dev/null || true
	_cleanup_lock
}

trap '_on_signal INT' INT
trap '_on_signal TERM' TERM
trap '' HUP
trap '_on_exit' EXIT
_acquire_runner_lock
echo "$$" >"$PID_FILE" 2>/dev/null || true

attempt=0
while true; do
	[ -f "$STOP_FILE" ] && exit 0

	attempt=$((attempt + 1))
	printf '[%s] [runner] launch attempt=%d\n' "$(date '+%H:%M:%S')" "$attempt" >>"$LOG_FILE" 2>/dev/null || true

	SOREN91_EXTERNAL_IMPROVE="${SOREN91_EXTERNAL_IMPROVE:-1}" node main.mjs >>"$LOG_FILE" 2>&1 &
	CHILD_MAIN_PID=$!
	wait "$CHILD_MAIN_PID"
	rc=$?
	CHILD_MAIN_PID=""
	rm -f "$MAIN_PID_FILE" 2>/dev/null || true

	[ -f "$STOP_FILE" ] && exit 0

	printf '[%s] [runner] node exited rc=%s, retry in %ss\n' \
		"$(date '+%H:%M:%S')" "$rc" "$RETRY_DELAY_SEC" >>"$LOG_FILE" 2>/dev/null || true
	sleep "$RETRY_DELAY_SEC"
done
