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

_STOPPED=0
_RELOAD_REQUESTED=0

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	_log "停止処理開始"
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
		POLL_INTERVAL="${AUDIO_WORKER_INTERVAL:-5}"
		_log "reload complete (interval=${POLL_INTERVAL}s)"
	else
		_log "WARNING: reload failed; keeping previous runtime"
	fi
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload HUP' HUP
trap '_request_reload USR1' USR1

# --- 多重起動防止 ---
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		_log "ERROR: 既に起動中 (PID=$old_pid)"
		exit 1
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" 2>/dev/null || true
echo $$ > "$PID_FILE"

# _play_comment_queue 内で使われる PID 変数を設定
_cp_my_pid=$$

# === メインループ ===
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s)"

_recover_orphan_comment_playing_files 2>/dev/null || true

while true; do
	_reload_runtime
	# 停止チェック
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# eloop_lib.sh を再読み込み (設定変更の反映)
	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi

	# === VOICEVOX failure check ===
	_say_fail_count=0
	_say_fail_count=$(grep -Ec 'VOICEVOX合成失敗|say起動失敗' tmp/.say_queue/debug.log 2>/dev/null | awk '{s+=$1}END{print s+0}')
	if [ "${_say_fail_count:-0}" -gt 3 ]; then
		_log "WARNING: say_enqueue ${_say_fail_count}件失敗中 — VOICEVOX(${VOICEVOX_HOST:-localhost:50021})への接続を確認してください"
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
