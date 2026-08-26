#!/bin/bash
# workers/kick_worker.sh - Kick チャットの受信とコメント生成を担当する foreground worker
#
# 責務:
#   1. Kick チャット ingest (kick_chat_daemon.mjs を子プロセスとして起動・死活監視)
#   2. コメント pending 管理 (kick_chat.sh fetch/ack-batch)
#   3. コメント生成 (generate_comment_response kick)
#
# 送信は Twitch/YouTube と同じ outbound queue が担当するため、この worker は
# 受信専用。起動: KICK_CHAT_ENABLED=1 ./workers/kick_worker.sh [slug]
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

source ./eloop_lib.sh

WORKER_NAME="kick_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
CHANNEL="${1:-${KICK_CHANNEL:-dociai}}"
POLL_INTERVAL="${KICK_CHAT_POLL_INTERVAL_SEC:-10}"
KICK_CHAT_DIR_PATH="${KICK_CHAT_DIR:-tmp/.kick_chat}"
KICK_DAEMON_PID_FILE="$KICK_CHAT_DIR_PATH/daemon.pid"
TMP_MARKERS_DIR="${TMP_MARKERS_DIR:-tmp/markers}"
TMP_DEBUG_DIR="${TMP_DEBUG_DIR:-tmp/debug}"

_DAEMON_PID=""
_STOPPED=0
_RELOAD_REQUESTED=0
_HEARTBEAT_PID=""

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
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ "$active_pid" != "$$" ]; then
		_log "cleanup skipped: pidfile owner is ${active_pid:-none} (self=$$)"
		return 0
	fi
	_log "停止処理開始"
	if [ -n "$_HEARTBEAT_PID" ]; then
		kill "$_HEARTBEAT_PID" 2>/dev/null || true
	fi
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		_log "chat daemon 停止 (PID=$_DAEMON_PID)"
		kill "$_DAEMON_PID" 2>/dev/null
		wait "$_DAEMON_PID" 2>/dev/null || true
	fi
	if [ -f "$KICK_DAEMON_PID_FILE" ]; then
		local recorded_daemon_pid
		recorded_daemon_pid=$(cat "$KICK_DAEMON_PID_FILE" 2>/dev/null || true)
		if [ "$recorded_daemon_pid" = "$_DAEMON_PID" ]; then
			rm -f "$KICK_DAEMON_PID_FILE"
		fi
	fi
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
		POLL_INTERVAL="${KICK_CHAT_POLL_INTERVAL_SEC:-$POLL_INTERVAL}"
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

if [ "${KICK_CHAT_ENABLED:-0}" != "1" ]; then
	_log "disabled (set KICK_CHAT_ENABLED=1)"
	exit 0
fi
if ! command -v node >/dev/null 2>&1; then
	_log "node が見つからないため起動できない"
	exit 0
fi

if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if _pid_alive "$old_pid"; then
		_log "already running (PID=$old_pid) -> no-op"
		exit 0
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" "$TMP_MARKERS_DIR" "$TMP_DEBUG_DIR" "$KICK_CHAT_DIR_PATH" 2>/dev/null || true
echo $$ >"$PID_FILE"
(
	while true; do
		echo $$ >"$PID_FILE" 2>/dev/null || true
		sleep "${WORKER_PID_HEARTBEAT_INTERVAL:-5}"
	done
) &
_HEARTBEAT_PID=$!

# --- chat daemon 起動 (子プロセスとして直接起動、nohup なし) ---
# chat_worker と同じ理由で、起動前に孤立インスタンスを掃除する。二重接続のまま
# 動くと同じコメントを二重に取り込む。
_start_chat_daemon() {
	./kick_chat.sh stop 2>/dev/null || true
	pkill -f 'kick_chat_daemon\.mjs' 2>/dev/null || true
	sleep 0.5

	mkdir -p "$KICK_CHAT_DIR_PATH"
	[ -f "$KICK_CHAT_DIR_PATH/raw.log" ] || touch "$KICK_CHAT_DIR_PATH/raw.log"
	[ -f "$KICK_CHAT_DIR_PATH/last_offset" ] || echo "0" >"$KICK_CHAT_DIR_PATH/last_offset"

	node ./kick_chat_daemon.mjs "$CHANNEL" >>"$KICK_CHAT_DIR_PATH/daemon.out" 2>&1 &
	_DAEMON_PID=$!
	echo "$_DAEMON_PID" >"$KICK_DAEMON_PID_FILE"
	_log "chat daemon 起動 (PID=$_DAEMON_PID, slug=$CHANNEL)"
}

_ensure_chat_daemon() {
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		return 0
	fi
	_log "chat daemon が停止 → 再起動"
	_start_chat_daemon
}

_is_comment_gen_running() {
	# コメント生成の排他は Twitch/YouTube と共有する (同時に2本生成しない)。
	local gen_pidfile="tmp/.twitch_chat/comment_gen.pid"
	[ -f "$gen_pidfile" ] || return 1
	local gen_pid
	gen_pid=$(cat "$gen_pidfile" 2>/dev/null)
	gen_pid="${gen_pid%%|*}"
	case "$gen_pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	_pid_alive "$gen_pid"
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

# --- pause gate (durable operator stop via tmp/state/<name>.paused) ---
_worker_is_paused() { [ -f "tmp/state/${WORKER_NAME}.paused" ]; }
_park_while_paused() {
	_worker_is_paused || return 0
	_log "paused (tmp/state/${WORKER_NAME}.paused) → アイドル待機 (マーカー削除で自動再開)"
	# 受信を止めるのが pause の意図なので、daemon も落としてから待つ。
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		kill "$_DAEMON_PID" 2>/dev/null || true
		wait "$_DAEMON_PID" 2>/dev/null || true
		_DAEMON_PID=""
	fi
	while _worker_is_paused; do
		[ -f tmp/stop ] && return 1
		echo $$ >"$PID_FILE" 2>/dev/null || true
		_reload_runtime 2>/dev/null || true
		sleep "${WORKER_PAUSE_POLL_SEC:-10}"
	done
	_log "resumed (marker removed) → 通常運転に復帰"
	return 0
}

_log "起動 (PID=$$, slug=$CHANNEL, interval=${POLL_INTERVAL}s)"

_worker_is_paused || _start_chat_daemon

while true; do
	echo $$ >"$PID_FILE" 2>/dev/null || true
	_reload_runtime
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	_park_while_paused || break

	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi
	if [ "${KICK_CHAT_ENABLED:-0}" != "1" ]; then
		_log "disabled by env → 終了"
		break
	fi

	_ensure_chat_daemon

	if _is_comment_gen_running; then
		# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
		./kick_chat.sh fetch 2>/dev/null || true
	else
		generate_comment_response kick 2>>"${AI_STDERR_LOG:-logs/ai_stderr.log}" || true
	fi

	_sleep_for_poll_interval "$POLL_INTERVAL"
done

_log "メインループ終了"
exit 0
