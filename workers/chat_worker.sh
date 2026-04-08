#!/bin/bash
# workers/chat_worker.sh - Twitch chat の受信・コメント生成・送信を統合する foreground worker
#
# 責務:
#   1. Twitch IRC ingest (twitch_chat_daemon.sh を子プロセスとして起動)
#   2. コメント pending 管理 (twitch_chat.sh fetch/ack-batch)
#   3. コメント生成 (generate_comment_response)
#   4. Outbound chat queue 消化 (enqueue されたメッセージを Twitch に送信)
#
# 起動: ./workers/chat_worker.sh [channel]
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ ---
source ./eloop_lib.sh

CHANNEL="${1:-azumagbanjo}"
WORKER_NAME="chat_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${COMMENT_WATCHER_INTERVAL:-10}"
OUTBOUND_CONSUME_MAX_PER_TICK=5
OUTBOUND_RATE_SEC=2

_DAEMON_PID=""
_STOPPED=0

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	_log "停止処理開始"

	# IRC daemon 子プロセスを停止
	if [ -n "$_DAEMON_PID" ] && kill -0 "$_DAEMON_PID" 2>/dev/null; then
		_log "IRC daemon 停止 (PID=$_DAEMON_PID)"
		kill "$_DAEMON_PID" 2>/dev/null
		wait "$_DAEMON_PID" 2>/dev/null || true
	fi

	# コメント生成プロセスが残っていたら停止
	_kill_comment_gen 2>/dev/null || true

	rm -f "$PID_FILE"
	_log "停止完了"
}

trap '_cleanup' EXIT INT TERM

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

# --- IRC daemon 起動 (子プロセスとして直接起動、nohup なし) ---
_start_irc_daemon() {
	# 既存 daemon が動いていたら停止
	./twitch_chat.sh stop 2>/dev/null || true

	# raw.log と offset を初期化 (存在しない場合のみ)
	local chat_dir="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
	mkdir -p "$chat_dir"
	[ -f "$chat_dir/raw.log" ] || touch "$chat_dir/raw.log"
	[ -f "$chat_dir/last_offset" ] || echo "0" > "$chat_dir/last_offset"

	bash ./twitch_chat_daemon.sh "$CHANNEL" &
	_DAEMON_PID=$!
	_log "IRC daemon 起動 (PID=$_DAEMON_PID, channel=$CHANNEL)"
}

# --- コメント生成が進行中かチェック ---
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

# --- Outbound queue 消化 (1 tick あたり最大 N 件) ---
_consume_outbound_queue() {
	local consumed=0
	while [ "$consumed" -lt "$OUTBOUND_CONSUME_MAX_PER_TICK" ]; do
		if outbound_queue_consume_once; then
			consumed=$((consumed + 1))
			sleep "$OUTBOUND_RATE_SEC"
		else
			break
		fi
	done
	# sent/ のクリーンアップ (TTL: 1時間)
	outbound_queue_cleanup_sent 3600 2>/dev/null || true
	return 0
}

# --- IRC daemon の死活監視 ---
_ensure_irc_daemon() {
	if [ -n "$_DAEMON_PID" ] && kill -0 "$_DAEMON_PID" 2>/dev/null; then
		return 0
	fi
	_log "WARN: IRC daemon が停止 → 再起動"
	_start_irc_daemon
}

# === メインループ ===
_log "起動 (PID=$$, channel=$CHANNEL, interval=${POLL_INTERVAL}s)"

_start_irc_daemon

while true; do
	# 停止チェック
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# eloop_lib.sh を再読み込み (設定変更の反映)
	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi

	# IRC daemon 死活監視
	_ensure_irc_daemon

	# コメント処理
	if _is_comment_gen_running; then
		# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
		./twitch_chat.sh fetch 2>/dev/null
	else
		# idle時は pending から生成
		generate_comment_response 2>/dev/null || true
	fi

	# Outbound queue 消化
	_consume_outbound_queue

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
