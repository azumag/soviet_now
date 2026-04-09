#!/bin/bash
# recover_main_loop.sh - soren91 / improve / daemon を安全停止し、必要ならメインループを再開

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a
source ./eloop_lib.sh

AUTO_START_LOOP=1
for arg in "$@"; do
	case "$arg" in
	--no-start)
		AUTO_START_LOOP=0
		;;
	--start)
		AUTO_START_LOOP=1
		;;
	*)
		echo "Usage: ./recover_main_loop.sh [--start|--no-start]" >&2
		exit 2
		;;
	esac
done

_read_main_loop_pid() {
	local pid=""
	if [ -f "tmp/.soren_loop.lock/pid" ]; then
		pid=$(cat "tmp/.soren_loop.lock/pid" 2>/dev/null || true)
	fi
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		return 1
	fi
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
	printf '%s' "$cmd" | grep -q "soren_loop\.sh" || return 1
	printf '%s\n' "$pid"
}

_stop_improve_daemon_only() {
	local pid=""
	if [ -f "$IMPROVE_DAEMON_PID_FILE" ]; then
		pid=$(cat "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null || true)
	fi
	case "$pid" in
	''|*[!0-9]*) pid="" ;;
	esac
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		local cmd
		cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
		if printf '%s' "$cmd" | grep -q "improve_daemon\.sh"; then
			log "[RECOVER] improve_daemon 停止 (PID=$pid)"
			_stop_pid_with_fallback "$pid" "improve_daemon"
		fi
	fi
	rm -f "$IMPROVE_DAEMON_PID_FILE"
}

main_loop_pid="$(_read_main_loop_pid 2>/dev/null || true)"
if [ -n "$main_loop_pid" ]; then
	log "[RECOVER] main loop detected (PID=$main_loop_pid)"
else
	log "[RECOVER] main loop not running"
fi

log "[RECOVER] stopping soren91"
soren91_stop 2>/dev/null || soren91_cleanup 2>/dev/null || true

log "[RECOVER] stopping improve job"
improve_pid="$(_find_live_improve_pid 2>/dev/null || true)"
case "$improve_pid" in
''|*[!0-9]*) improve_pid="" ;;
esac
if [ -n "$improve_pid" ]; then
	_stop_improve_pid_if_running "$improve_pid" "recover_improve" || true
fi

log "[RECOVER] stopping improve daemon"
_stop_improve_daemon_only

log "[RECOVER] resetting improve state"
_write_improve_state "idle" "0" "" "recovered" "0" "manual_recover"
rm -f "$IMPROVE_LOCK_FILE"
rm -f "$TMP_STATE_DIR/last_improve_failed_at" "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || true
IMPROVE_PID=0

if [ -n "$main_loop_pid" ] && kill -0 "$main_loop_pid" 2>/dev/null; then
	log "[RECOVER] main loop is alive -> it should resume automatically within a few seconds"
	exit 0
fi

if [ "$AUTO_START_LOOP" != "1" ]; then
	log "[RECOVER] main loop is stopped. restart skipped by --no-start"
	exit 0
fi

mkdir -p "$TMP_DEBUG_DIR" 2>/dev/null || true
resume_log="$TMP_DEBUG_DIR/soren_loop_resume.log"
log "[RECOVER] restarting main loop"
nohup /bin/bash ./soren_loop.sh >>"$resume_log" 2>&1 &
new_pid=$!
disown "$new_pid" 2>/dev/null || true
sleep 1
if kill -0 "$new_pid" 2>/dev/null; then
	log "[RECOVER] main loop restarted (PID=$new_pid, log=$resume_log)"
	exit 0
fi

log "[RECOVER] failed to restart main loop"
exit 1
