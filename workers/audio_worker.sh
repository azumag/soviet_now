#!/bin/bash
# workers/audio_worker.sh - 音声再生の foreground worker
#
# 責務:
#   1. コメント playback queue 消化 (comment_*.txt → say_enqueue.sh)
#   2. Deferred radio queue 再生
#   3. External audio trigger 消化
#
# 既存の _play_comment_queue() が上記全てを統合しているため、
# このworkerは薄いオーケストレータとしてそれをループ実行する。
#
# 起動: ./workers/audio_worker.sh
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ ---
source ./eloop_lib.sh

WORKER_NAME="audio_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${AUDIO_WORKER_INTERVAL:-5}"
WARNING_INTERVAL="${AUDIO_WORKER_WARNING_INTERVAL_SEC:-900}"

_STOPPED=0
_RELOAD_REQUESTED=0
_LAST_SAY_WARNING_TS=0
_LAST_SAY_WARNING_COUNT=0
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

_count_consecutive_terminal_say_failures() {
	local played_log="${1:-tmp/.say_queue/played.log}"
	[ -f "$played_log" ] || {
		printf '0\n'
		return 0
	}
	tail -100 "$played_log" 2>/dev/null | awk '
		$2 == "failed" { failures += 1; next }
		$2 == "played" || $2 ~ /^skipped_/ { failures = 0 }
		END { print failures + 0 }
	'
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
	rm -f "$PID_FILE"
	if [ -n "${LOCK_DIR:-}" ] && [ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" = "$$" ]; then
		rm -f "$LOCK_DIR/pid" 2>/dev/null || true
		rmdir "$LOCK_DIR" 2>/dev/null || true
	fi
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
		POLL_INTERVAL="${AUDIO_WORKER_INTERVAL:-5}"
		WARNING_INTERVAL="${AUDIO_WORKER_WARNING_INTERVAL_SEC:-900}"
		_log "reload complete (interval=${POLL_INTERVAL}s, warning_interval=${WARNING_INTERVAL}s)"
	else
		_log "WARNING: reload failed; keeping previous runtime"
	fi
}

_ensure_single_owner() {
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ -n "$active_pid" ] && [ "$active_pid" != "$$" ] && _pid_alive "$active_pid"; then
		_log "another ${WORKER_NAME} owns pidfile (owner=${active_pid}, self=$$) -> exit"
		_cleanup
		trap - EXIT
		exit 0
	fi
	echo $$ > "$PID_FILE"
}
_start_pid_heartbeat() {
	(
		while true; do
			echo $$ >"$PID_FILE" 2>/dev/null || true
			sleep "${WORKER_PID_HEARTBEAT_INTERVAL:-5}"
		done
	) &
	_HEARTBEAT_PID=$!
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload HUP' HUP
trap '_request_reload USR1' USR1

# --- 多重起動防止 (mkdir アトミックロック + PID検証) ---
mkdir -p "$(dirname "$PID_FILE")" 2>/dev/null || true
LOCK_DIR="${PID_FILE}.lock"
if mkdir "$LOCK_DIR" 2>/dev/null; then
	printf '%s\n' "$$" >"$LOCK_DIR/pid" 2>/dev/null || true
else
	# ロックが既にある: 所有者が生きていれば no-op、死んでいれば stale として奪取
	lock_owner=""
	[ -f "$LOCK_DIR/pid" ] && lock_owner=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
	case "$lock_owner" in '' | *[!0-9]*) lock_owner="" ;; esac
	if [ -n "$lock_owner" ] && _pid_alive "$lock_owner"; then
		_log "already running (PID=$lock_owner) -> no-op"
		exit 0
	fi
	# stale ロックを回収して再取得
	rm -rf "$LOCK_DIR" 2>/dev/null || true
	if mkdir "$LOCK_DIR" 2>/dev/null; then
		printf '%s\n' "$$" >"$LOCK_DIR/pid" 2>/dev/null || true
	else
		_log "lock race lost -> no-op"
		exit 0
	fi
fi
# pidfile は既存互換のためロック所有者PIDを書き、heartbeat も同じPIDを維持する
echo $$ > "$PID_FILE"
_start_pid_heartbeat

# _play_comment_queue 内で使われる PID 変数を設定
_cp_my_pid=$$

# --- pause gate (durable operator stop via tmp/state/<name>.paused) ---
_worker_is_paused() { [ -f "tmp/state/${WORKER_NAME}.paused" ]; }
_park_while_paused() {
	# Idle quietly while the pause marker exists (no playback). Process stays
	# alive so the supervisor keeps adopting it (no respawn storm). Auto-resumes
	# when the marker is removed. Returns 1 if tmp/stop appeared.
	_worker_is_paused || return 0
	_log "paused (tmp/state/${WORKER_NAME}.paused) → アイドル待機 (作業停止・マーカー削除で自動再開)"
	while _worker_is_paused; do
		[ -f tmp/stop ] && return 1
		echo $$ >"$PID_FILE" 2>/dev/null || true
		_reload_runtime 2>/dev/null || true
		sleep "${WORKER_PAUSE_POLL_SEC:-10}"
	done
	_log "resumed (marker removed) → 通常運転に復帰"
	return 0
}

# === メインループ ===
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s)"

_recover_orphan_comment_playing_files 2>/dev/null || true

while true; do
	_ensure_single_owner
	_reload_runtime
	# 停止チェック
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# pause gate (durable operator stop): idle without doing work
	_park_while_paused || break

	# eloop_lib.sh を再読み込み (設定変更の反映)
	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi

	# === VOICEVOX failure check ===
	_say_fail_count=0
	_say_fail_count=$(_count_consecutive_terminal_say_failures "tmp/.say_queue/played.log")
	_say_fail_threshold="${AUDIO_WORKER_FAILURE_WARNING_THRESHOLD:-3}"
	case "$_say_fail_threshold" in '' | *[!0-9]*) _say_fail_threshold=3 ;; esac
	[ "$_say_fail_threshold" -gt 0 ] || _say_fail_threshold=1
	if [ "${_say_fail_count:-0}" -ge "$_say_fail_threshold" ]; then
		_now_ts=$(date +%s)
		case "$WARNING_INTERVAL" in ''|*[!0-9]*) WARNING_INTERVAL=900 ;; esac
		if [ "$_say_fail_count" != "$_LAST_SAY_WARNING_COUNT" ] || [ $((_now_ts - _LAST_SAY_WARNING_TS)) -ge "${WARNING_INTERVAL:-900}" ]; then
			_LAST_SAY_WARNING_TS="$_now_ts"
			_LAST_SAY_WARNING_COUNT="$_say_fail_count"
			_log "WARNING: say_enqueue が${_say_fail_count}件連続で最終失敗 — VOICEVOX(${VOICEVOX_HOST:-localhost:50021})と再生経路を確認してください"
		fi
	else
		_LAST_SAY_WARNING_COUNT=0
	fi

	# 再生処理: コメント → external trigger → deferred radio
	_play_comment_queue 2>/dev/null || true

	# sleep を1秒単位で分割し、tmp/stop を素早く検知できるようにする
	_sleep_remaining="$POLL_INTERVAL"
	while [ "${_sleep_remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && break 2
		sleep 1
		_sleep_remaining=$((_sleep_remaining - 1))
	done
done

_log "メインループ終了"
exit 0
