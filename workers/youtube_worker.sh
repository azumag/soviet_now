#!/bin/bash
# workers/youtube_worker.sh - YouTube Live Chat の受信とコメント生成を担当する foreground worker
#
# 起動: YOUTUBE_CHAT_ENABLED=1 ./workers/youtube_worker.sh
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

source ./eloop_lib.sh

WORKER_NAME="youtube_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${YOUTUBE_CHAT_POLL_INTERVAL_SEC:-10}"
TMP_MARKERS_DIR="${TMP_MARKERS_DIR:-tmp/markers}"
TMP_DEBUG_DIR="${TMP_DEBUG_DIR:-tmp/debug}"

_STOPPED=0
_RELOAD_REQUESTED=0

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	_kill_comment_gen 2>/dev/null || true
	rm -f "$PID_FILE"
	_log "停止完了"
}

_handle_signal() {
	_cleanup
	trap - EXIT
	exit 130
}

_request_reload() {
	_RELOAD_REQUESTED=1
	_log "reload requested (signal=$1)"
}

_reload_runtime() {
	[ "$_RELOAD_REQUESTED" -eq 1 ] || return 0
	_RELOAD_REQUESTED=0
	if [ -f .env ]; then
		set -a
		. ./.env
		set +a
	fi
	if source ./eloop_lib.sh 2>/dev/null; then
		POLL_INTERVAL="${YOUTUBE_CHAT_POLL_INTERVAL_SEC:-$POLL_INTERVAL}"
		TMP_MARKERS_DIR="${TMP_MARKERS_DIR:-tmp/markers}"
		TMP_DEBUG_DIR="${TMP_DEBUG_DIR:-tmp/debug}"
		mkdir -p "$TMP_MARKERS_DIR" "$TMP_DEBUG_DIR" 2>/dev/null || true
		_log "reload complete (interval=${POLL_INTERVAL}s)"
	else
		_log "WARNING: reload failed; keeping previous runtime"
	fi
}

trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload HUP' HUP
trap '_request_reload USR1' USR1

if [ "${YOUTUBE_CHAT_ENABLED:-0}" != "1" ]; then
	_log "disabled (set YOUTUBE_CHAT_ENABLED=1)"
	exit 0
fi

if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		_log "ERROR: 既に起動中 (PID=$old_pid)"
		exit 1
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" "$TMP_MARKERS_DIR" "$TMP_DEBUG_DIR" tmp/.youtube_chat 2>/dev/null || true
echo $$ >"$PID_FILE"

_is_comment_gen_running() {
	local gen_pidfile="tmp/.twitch_chat/comment_gen.pid"
	[ -f "$gen_pidfile" ] || return 1
	local gen_pid
	gen_pid=$(cat "$gen_pidfile" 2>/dev/null)
	gen_pid="${gen_pid%%|*}"
	case "$gen_pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	kill -0 "$gen_pid" 2>/dev/null
}

_sleep_for_poll_interval() {
	local interval="$1"
	case "$interval" in
	''|*[!0-9]*) interval="$POLL_INTERVAL" ;;
	esac
	[ "$interval" -lt 2 ] 2>/dev/null && interval=2
	local remaining="$interval"
	while [ "${remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && return 0
		sleep 1
		remaining=$((remaining - 1))
	done
}

_log "起動 (PID=$$, interval=${POLL_INTERVAL}s)"

while true; do
	_reload_runtime
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi
	if [ -f .env ]; then
		set -a
		. ./.env
		set +a
	fi
	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi
	if [ "${YOUTUBE_CHAT_ENABLED:-0}" != "1" ]; then
		_log "disabled by env → 終了"
		break
	fi

	./youtube_chat.sh poll 2>/dev/null || true
	if _is_comment_gen_running; then
		./youtube_chat.sh fetch 2>/dev/null || true
	else
		generate_comment_response youtube 2>/dev/null || true
	fi

	interval="$POLL_INTERVAL"
	if [ -f tmp/.youtube_chat/poll_interval_sec ]; then
		interval=$(cat tmp/.youtube_chat/poll_interval_sec 2>/dev/null || echo "$POLL_INTERVAL")
	fi
	if [ "${interval:-0}" -lt "${POLL_INTERVAL:-10}" ] 2>/dev/null; then
		interval="$POLL_INTERVAL"
	fi
	_sleep_for_poll_interval "$interval"
done

exit 0
