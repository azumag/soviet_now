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
		mt=$(stat -f %m "$RUNNER_LOCK_DIR" 2>/dev/null) \
			|| mt=$(stat -c %Y "$RUNNER_LOCK_DIR" 2>/dev/null) \
			|| mt="$now"
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

# The lock is only checked once at startup (_acquire_runner_lock). A runner then
# loops forever, so if the lock dir is later cleared (stale cleanup /
# soren91_cleanup) and a NEW runner acquires it, the old runner keeps running too
# -> accumulated multi-runner flapping (observed: 6 concurrent runners). Re-assert
# ownership each iteration: if a DIFFERENT live runner owns the lock, defer to it
# and exit; if the lock is gone / its owner is dead, reclaim it for ourselves so
# exactly the latest runner survives.
_runner_still_owner() {
	local owner=""
	owner=$(sed -n 's/^pid=//p' "$RUNNER_LOCK_DIR/owner" 2>/dev/null | head -n 1)
	if [ -n "$owner" ] && [ "$owner" != "$$" ] && _pid_alive "$owner"; then
		return 1
	fi
	if [ "$owner" != "$$" ]; then
		mkdir -p "$RUNNER_LOCK_DIR" 2>/dev/null || true
		{
			printf 'pid=%s\n' "$$"
			printf 'started_at=%s\n' "$(date '+%F %T')"
		} >"$RUNNER_LOCK_DIR/owner" 2>/dev/null || true
	fi
	return 0
}

trap '_on_signal INT' INT
trap '_on_signal TERM' TERM
trap '' HUP
trap '_on_exit' EXIT
_acquire_runner_lock
echo "$$" >"$PID_FILE" 2>/dev/null || true

# rc=0-即時終了は「今は走るべきでない」(共有Chrome attach失敗等を main().catch が
# 握り潰して rc=0 終了する) を意味する。3s固定で再試行すると、共有Chrome不安定時に
# 1秒未満で死ぬ→3sで再試行を無限ループし、候補chrome群と GUI登録を奪い合って
# crash/flapping を悪化させる。即時終了が連続したら指数バックオフ(上限付き)で
# リトライ間隔を伸ばす。十分長く走った(=実ゲーム)場合はカウンタをリセットする。
FAST_EXIT_THRESHOLD_SEC="${SOREN91_FAST_EXIT_THRESHOLD_SEC:-20}"
FAST_EXIT_BACKOFF_MAX_SEC="${SOREN91_FAST_EXIT_BACKOFF_MAX_SEC:-60}"
fast_exit_streak=0

attempt=0
while true; do
	[ -f "$STOP_FILE" ] && exit 0
	if ! _runner_still_owner; then
		printf '[%s] [runner] another live runner owns the lock; exiting to avoid multi-runner flapping\n' \
			"$(date '+%H:%M:%S')" >>"$LOG_FILE" 2>/dev/null || true
		exit 0
	fi

	attempt=$((attempt + 1))
	printf '[%s] [runner] launch attempt=%d\n' "$(date '+%H:%M:%S')" "$attempt" >>"$LOG_FILE" 2>/dev/null || true

	run_start=$(date +%s)
	SOREN91_EXTERNAL_IMPROVE="${SOREN91_EXTERNAL_IMPROVE:-1}" node main.mjs >>"$LOG_FILE" 2>&1 &
	CHILD_MAIN_PID=$!
	wait "$CHILD_MAIN_PID"
	rc=$?
	CHILD_MAIN_PID=""
	rm -f "$MAIN_PID_FILE" 2>/dev/null || true

	[ -f "$STOP_FILE" ] && exit 0

	run_dur=$(( $(date +%s) - run_start ))
	retry_delay="$RETRY_DELAY_SEC"
	if [ "$run_dur" -lt "$FAST_EXIT_THRESHOLD_SEC" ]; then
		fast_exit_streak=$((fast_exit_streak + 1))
		# 指数バックオフ: RETRY_DELAY_SEC * 2^(streak-1)、FAST_EXIT_BACKOFF_MAX_SEC で頭打ち
		retry_delay=$((RETRY_DELAY_SEC << (fast_exit_streak - 1)))
		[ "$retry_delay" -gt "$FAST_EXIT_BACKOFF_MAX_SEC" ] && retry_delay="$FAST_EXIT_BACKOFF_MAX_SEC"
		[ "$retry_delay" -lt "$RETRY_DELAY_SEC" ] && retry_delay="$RETRY_DELAY_SEC"
		printf '[%s] [runner] node exited rc=%s after %ss (fast-exit streak=%d), backing off %ss\n' \
			"$(date '+%H:%M:%S')" "$rc" "$run_dur" "$fast_exit_streak" "$retry_delay" >>"$LOG_FILE" 2>/dev/null || true
	else
		fast_exit_streak=0
		printf '[%s] [runner] node exited rc=%s after %ss, retry in %ss\n' \
			"$(date '+%H:%M:%S')" "$rc" "$run_dur" "$retry_delay" >>"$LOG_FILE" 2>/dev/null || true
	fi
	sleep "$retry_delay"
done
