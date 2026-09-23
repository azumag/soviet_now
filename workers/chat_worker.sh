#!/bin/bash
# workers/chat_worker.sh - Twitch chat の受信・コメント生成・送信・クリップ作成を統合する foreground worker
#
# 責務:
#   1. Twitch IRC ingest (twitch_chat_daemon.sh を子プロセスとして起動)
#   2. コメント pending 管理 (twitch_chat.sh fetch/ack-batch)
#   3. コメント生成 (generate_comment_response)
#   4. Outbound chat queue 消化 (enqueue されたメッセージを Twitch に送信)
#   5. Clip queue 消化 (tmp/clip_queue/ のイベントを処理して twitch_clip.sh を実行)
#
# 起動: ./workers/chat_worker.sh [channel]
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み ---
# The supervisor may be older than the projected worker file and can retain
# classifier variables from its launch environment.  The .env file is the
# source of truth for this worker: clear the managed names before sourcing it,
# then re-exec once so /proc and future children carry the effective values.
unset \
	COMMENT_CLASSIFIER_BACKEND \
	COMMENT_CLASSIFIER_JEV_MODEL \
	COMMENT_CLASSIFIER_JEV_TIMEOUT_MS \
	COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE \
	COMMENT_CLASSIFIER_JEV_LOG_ENABLED \
	DOCICH_SEMANTIC_BACKEND \
	DOCICH_JEV_ROUTE \
	DOCICH_JEV_VERCEL_API_KEY \
	TYPESAFE_API_KEY
[ -f .env ] && set -a && . ./.env && set +a
if [ "${CHAT_WORKER_ENV_REEXEC:-0}" != "1" ]; then
	export CHAT_WORKER_ENV_REEXEC=1
	exec /bin/bash "$SCRIPT_DIR/workers/chat_worker.sh" "$@"
fi
unset CHAT_WORKER_ENV_REEXEC

# --- 共通ライブラリ ---
source ./eloop_lib.sh

CHANNEL="${1:-azumagbanjo}"
WORKER_NAME="chat_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
TWITCH_DAEMON_PID_FILE="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}/daemon.pid"
POLL_INTERVAL="${COMMENT_WATCHER_INTERVAL:-10}"
OUTBOUND_CONSUME_MAX_PER_TICK=5
OUTBOUND_RATE_SEC=2
CLIP_QUEUE_DIR="tmp/clip_queue"
CLIP_QUEUE_DONE_DIR="tmp/clip_queue/done"
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

	# IRC daemon 子プロセスを停止
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		_log "IRC daemon 停止 (PID=$_DAEMON_PID)"
		kill "$_DAEMON_PID" 2>/dev/null
		wait "$_DAEMON_PID" 2>/dev/null || true
	fi
	if [ -f "$TWITCH_DAEMON_PID_FILE" ]; then
		local recorded_daemon_pid
		recorded_daemon_pid=$(cat "$TWITCH_DAEMON_PID_FILE" 2>/dev/null || true)
		if [ "$recorded_daemon_pid" = "$_DAEMON_PID" ]; then
			rm -f "$TWITCH_DAEMON_PID_FILE"
		fi
	fi

	# コメント生成プロセスが残っていたら停止
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
		POLL_INTERVAL="${COMMENT_WATCHER_INTERVAL:-10}"
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

# --- 多重起動防止 ---
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if _pid_alive "$old_pid"; then
		_log "already running (PID=$old_pid) -> no-op"
		exit 0
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" "$CLIP_QUEUE_DIR" "$CLIP_QUEUE_DONE_DIR" "$TMP_MARKERS_DIR" "$TMP_DEBUG_DIR" 2>/dev/null || true
echo $$ > "$PID_FILE"
(
	while true; do
		echo $$ >"$PID_FILE" 2>/dev/null || true
		sleep "${WORKER_PID_HEARTBEAT_INTERVAL:-5}"
	done
) &
_HEARTBEAT_PID=$!

# --- IRC daemon 起動 (子プロセスとして直接起動、nohup なし) ---
_start_irc_daemon() {
	# 既存 daemon が動いていたら停止
	./twitch_chat.sh stop 2>/dev/null || true
	# 2026-05-31 fix: chat_worker が高速再起動されると PID ファイルが上書きされ
	# 以前の twitch_chat_daemon.sh が孤立(PPID=1)したまま残る→IRC接続が複数になり
	# 同じコメント返答が N 回送信される。stop の後に全インスタンスを pkill で掃除。
	pkill -f 'twitch_chat_daemon\.sh' 2>/dev/null || true
	sleep 0.5

	# raw.log と offset を初期化 (存在しない場合のみ)
	local chat_dir="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
	mkdir -p "$chat_dir"
	[ -f "$chat_dir/raw.log" ] || touch "$chat_dir/raw.log"
	[ -f "$chat_dir/last_offset" ] || echo "0" > "$chat_dir/last_offset"

	bash ./twitch_chat_daemon.sh "$CHANNEL" &
	_DAEMON_PID=$!
	echo "$_DAEMON_PID" > "$TWITCH_DAEMON_PID_FILE"
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
	_pid_alive "$gen_pid"
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

# --- Clip queue 消化 ---
_process_clip_queue() {
	local queue_file
	local failed_dir="$CLIP_QUEUE_DIR/failed"
	mkdir -p "$failed_dir" || return 1
	for queue_file in "$CLIP_QUEUE_DIR"/*.json; do
		[ -f "$queue_file" ] || continue

		# JSON パース
		local event_msg game_id delay event_kind attempts parsed
		parsed=$(python3 -c "
import json, sys, shlex
d = json.load(open(sys.argv[1]))
assert isinstance(d, dict)
assert str(d.get('game_id', '')).isdigit() or not d.get('game_id')
assert isinstance(d.get('event_msg', ''), str)
assert isinstance(d.get('event_kind', 'generic'), str)
assert isinstance(d.get('delay', 0), int) and 0 <= d.get('delay', 0) <= 300
assert isinstance(d.get('attempts', 0), int) and 0 <= d.get('attempts', 0) <= 3
print(f'event_msg={shlex.quote(d.get(\"event_msg\",\"\"))}')
print(f'game_id={shlex.quote(str(d.get(\"game_id\",\"\")))}')
print(f'delay={shlex.quote(str(d.get(\"delay\",0)))}')
print(f'event_kind={shlex.quote(d.get(\"event_kind\",\"generic\"))}')
print(f'attempts={d.get(\"attempts\",0)}')
" "$queue_file" 2>/dev/null) || {
			_log "WARN: clip parse failed: $(basename "$queue_file") → failed"
			mv "$queue_file" "$failed_dir/" 2>/dev/null || true
			continue
		}
		eval "$parsed"

		# ソ連建国は同じ試合の一般クリップに抑止されない。
		# ソ連建国済みなら、その後のハイスコア等は従来どおり抑止する。
		local clip_marker=""
		if [ -n "$game_id" ]; then
			clip_marker="$TMP_MARKERS_DIR/.twitch_clip_game_${game_id}"
			if [ "$event_kind" = "soviet" ]; then
				clip_marker="${clip_marker}_soviet"
			elif [ -d "${clip_marker}_soviet" ]; then
				_log "clip skip: Soviet clip already claimed for game $game_id"
				if [ -f "${clip_marker}_soviet/failed_rc" ]; then
					mv "$queue_file" "$failed_dir/" 2>/dev/null || true
				else
					mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || true
				fi
				continue
			fi
			if ! mkdir "$clip_marker" 2>/dev/null; then
				_log "clip skip: already claimed for game $game_id"
				if [ -f "$clip_marker/failed_rc" ]; then
					mv "$queue_file" "$failed_dir/" 2>/dev/null || true
				else
					mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || true
				fi
				continue
			fi
		fi

		# delay 待機 (1秒単位で stop チェック)
		if [ "${delay:-0}" -gt 0 ] 2>/dev/null; then
			_log "clip waiting ${delay}s (game=${game_id:-?})"
			local waited=0
			while [ "$waited" -lt "$delay" ]; do
				if [ -f tmp/stop ]; then
					[ -z "$clip_marker" ] || rmdir "$clip_marker" 2>/dev/null || true
					return 0
				fi
				sleep 1
				waited=$((waited + 1))
			done
		fi

		_log "clip creating: ${event_msg} (game=${game_id:-?})"
		local clip_rc=0
		./twitch_clip.sh "$event_msg" "$event_kind" 2>>"$TMP_DEBUG_DIR/twitch_clip.log" || clip_rc=$?
		attempts=$((attempts + 1))
		if [ "$clip_rc" -eq 75 ] && [ "$attempts" -lt 3 ]; then
			# 未作成と判明した一時障害だけ次のtickで再試行する。
			# POST timeoutなど結果不明の失敗は、自動再作成しない。
			if python3 - "$queue_file" "$attempts" <<'PY'
import json, os, sys
path, attempts = sys.argv[1:]
with open(path) as f:
    event = json.load(f)
event.update(attempts=int(attempts), delay=0)
temporary = path + '.retry'
with open(temporary, 'w') as f:
    json.dump(event, f, ensure_ascii=False)
os.replace(temporary, path)
PY
			then
				[ -z "$clip_marker" ] || rmdir "$clip_marker" 2>/dev/null || true
				_log "clip retry scheduled (game=${game_id:-?}, attempt=$attempts/3)"
				return 0
			fi
		fi
		if [ "$clip_rc" -ne 0 ]; then
			_log "WARN: clip failed (game=${game_id:-?}, rc=$clip_rc, attempts=$attempts)"
			[ -z "$clip_marker" ] || printf '%s\n' "$clip_rc" > "$clip_marker/failed_rc"
			mv "$queue_file" "$failed_dir/" 2>/dev/null || true
			continue
		fi

		mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || rm -f "$queue_file"
	done

	# done/ クリーンアップ (1時間超)
	find "$CLIP_QUEUE_DONE_DIR" -name '*.json' -mmin +60 -delete 2>/dev/null || true
}

# --- IRC daemon の死活監視 ---
_ensure_irc_daemon() {
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		return 0
	fi
	_log "WARN: IRC daemon が停止 → 再起動"
	_start_irc_daemon
}

# --- pause gate (durable operator stop via tmp/state/<name>.paused) ---
_worker_is_paused() { [ -f "tmp/state/${WORKER_NAME}.paused" ]; }
_park_while_paused() {
	# Idle quietly while the pause marker exists; release the IRC daemon so no
	# chat ingest/replies happen. The process stays alive so the supervisor keeps
	# adopting it (no respawn storm) — it just does no work. Auto-resumes when the
	# marker is removed. Returns 1 if tmp/stop appeared (caller should break).
	_worker_is_paused || return 0
	if [ -n "$_DAEMON_PID" ] && _pid_alive "$_DAEMON_PID"; then
		_log "paused: IRC daemon 停止 (PID=$_DAEMON_PID)"
		kill "$_DAEMON_PID" 2>/dev/null || true
		wait "$_DAEMON_PID" 2>/dev/null || true
	fi
	_DAEMON_PID=""
	pkill -f 'twitch_chat_daemon\.sh' 2>/dev/null || true
	rm -f "$TWITCH_DAEMON_PID_FILE" 2>/dev/null || true
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
_log "起動 (PID=$$, channel=$CHANNEL, interval=${POLL_INTERVAL}s)"

_worker_is_paused || _start_irc_daemon

while true; do
	echo $$ >"$PID_FILE" 2>/dev/null || true
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

	# IRC daemon 死活監視
	_ensure_irc_daemon

	# コメント処理
	if _is_comment_gen_running; then
		# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
		./twitch_chat.sh fetch 2>/dev/null
	else
		# idle時は pending から生成
		generate_comment_response 2>>"${AI_STDERR_LOG:-logs/ai_stderr.log}" || true
	fi
	[ -x ./codex_bug_dispatcher.sh ] && ./codex_bug_dispatcher.sh kick >/dev/null 2>&1 || true

	# Outbound queue 消化
	_consume_outbound_queue

	# Clip queue 消化
	_process_clip_queue

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
