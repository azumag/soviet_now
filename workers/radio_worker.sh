#!/bin/bash
# workers/radio_worker.sh - ラジオ生成スケジュールの foreground worker
#
# 責務:
#   1. ゲーム進行 (game_count.txt) を監視し、新しい試合開始を検知
#   2. schedule_nonessential_audio_jobs() を呼んでラジオ生成をトリガー
#   3. 時刻ベースのコーナーも同関数が内部で処理する
#
# 生成されたラジオ本文は deferred radio queue に積まれ、
# audio_worker が再生する。
#
# 起動: ./workers/radio_worker.sh
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- ログ出力 ---
# supervisor 起動時は start_all.sh が logs/radio_worker.log へ標準出力を保存する。
# ここで process substitution tee を使うと macOS/Codex sandbox の /dev/fd 制限で
# duplicate 起動の即時終了時に "Operation not permitted" が出るため、worker 側では
# 標準出力を差し替えない。
mkdir -p tmp 2>/dev/null || true

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ ---
source ./eloop_lib.sh

WORKER_NAME="radio_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${RADIO_WORKER_INTERVAL:-10}"

_STOPPED=0
_RELOAD_REQUESTED=0
_HEARTBEAT_PID=""
_LAST_GAME_NUM=""
_LAST_SCHEDULER_RUN_FILE="tmp/state/.last_scheduler_run"
_SCHEDULER_INTERVAL_SEC="${RADIO_WORKER_SCHEDULER_INTERVAL:-300}" # 5分ごとに時刻ベース実行

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

# 独立 worker: soren_loop の状態は一切参照しない。停止は tmp/stop か自プロセスへのシグナルのみ。
_DIAG_LOG="tmp/radio_worker_shutdown.log"
_LAST_SIGNAL=""

_dump_diag() {
	local cause="$1"
	{
		echo "===== $(date '+%F %T') shutdown ====="
		echo "cause:       $cause"
		echo "pid:         $$ (BASHPID=${BASHPID:-?})"
		echo "ppid:        $PPID"
		echo "last_cmd:    $BASH_COMMAND"
		echo "FUNCNAME:    ${FUNCNAME[*]}"
		echo "BASH_SOURCE: ${BASH_SOURCE[*]}"
		echo "BASH_LINENO: ${BASH_LINENO[*]}"
		echo "ps -p $$:"
		ps -p $$ -o pid,ppid,stat,etime,command 2>/dev/null
		echo
	} >>"$_DIAG_LOG" 2>&1 || true
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
	_dump_diag "${_LAST_SIGNAL:-EXIT}"
	_log "shutdown: ${WORKER_NAME} 停止 (cause=${_LAST_SIGNAL:-EXIT})"
	if [ -n "$_HEARTBEAT_PID" ]; then
		kill "$_HEARTBEAT_PID" 2>/dev/null || true
	fi
	rm -f "$PID_FILE"
}

_handle_signal() {
	_LAST_SIGNAL="$1"
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
		POLL_INTERVAL="${RADIO_WORKER_INTERVAL:-10}"
		_SCHEDULER_INTERVAL_SEC="${RADIO_WORKER_SCHEDULER_INTERVAL:-300}"
		_log "reload complete (interval=${POLL_INTERVAL}s, scheduler_interval=${_SCHEDULER_INTERVAL_SEC}s)"
	else
		_log "WARNING: reload failed; keeping previous runtime"
	fi
}

_ensure_single_owner() {
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ -n "$active_pid" ] && [ "$active_pid" != "$$" ] && _pid_alive "$active_pid"; then
		_log "another ${WORKER_NAME} owns pidfile (owner=${active_pid}, self=$$) -> exit"
		_LAST_SIGNAL="PIDFILE_OWNER_CHANGED"
		_cleanup
		trap - EXIT
		exit 0
	fi
	echo $$ >"$PID_FILE"
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
trap '_handle_signal INT' INT
trap '_handle_signal TERM' TERM
trap '_request_reload HUP' HUP
trap ':' PIPE
trap '_request_reload USR1' USR1
trap '_request_reload USR2' USR2

# --- 多重起動防止 ---
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if _pid_alive "$old_pid"; then
		_log "already running (PID=$old_pid) -> no-op"
		exit 0
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" 2>/dev/null || true
echo $$ >"$PID_FILE"
_start_pid_heartbeat

# 初期 game_num
_LAST_GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

# === ワーカーループ ===
# soren_loop や他の worker の状態は一切参照しない。
# 明示停止 (tmp/stop / SIGINT / SIGTERM) 以外では絶対に終わらない。
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s, initial_game=${_LAST_GAME_NUM})"

_run_iteration() {
	# 1 回分の処理。どこで失敗しても呼び出し元には影響させない (|| true で吸収)
	if ! (source ./eloop_lib.sh) 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi

	local current_game_num score
	current_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

	local _scheduler_ran_this_tick=0
	if [ "$current_game_num" != "$_LAST_GAME_NUM" ]; then
		_log "新試合検知: ${_LAST_GAME_NUM} → ${current_game_num}"
		_LAST_GAME_NUM="$current_game_num"
		score=$(_last_score 2>/dev/null || echo 0)
		schedule_nonessential_audio_jobs "$current_game_num" "$score" 2>>"${AI_STDERR_LOG:-logs/ai_stderr.log}" || true
		_scheduler_ran_this_tick=1
	fi

	# 時刻ベース定期実行 (5 分ごと) — 同一 tick で新試合から既に実行済みならスキップ
	local _now_ts _last_run=0
	_now_ts=$(date +%s)
	[ -f "$_LAST_SCHEDULER_RUN_FILE" ] && _last_run=$(cat "$_LAST_SCHEDULER_RUN_FILE" 2>/dev/null || echo 0)
	if [ "$_scheduler_ran_this_tick" -eq 0 ] && [ $((_now_ts - _last_run)) -ge $_SCHEDULER_INTERVAL_SEC ]; then
		_log "時刻ベースラジオ実行 ($((_now_ts - _last_run))s経過)"
		echo "$_now_ts" >"$_LAST_SCHEDULER_RUN_FILE"
		current_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
		score=$(_last_score 2>/dev/null || echo 0)
		schedule_nonessential_audio_jobs "$current_game_num" "$score" 2>>"${AI_STDERR_LOG:-logs/ai_stderr.log}" || true
	fi

	# 手動トリガー消化
	score=$(_last_score 2>/dev/null || echo 0)
	process_external_audio_triggers "$current_game_num" "$score" 2>/dev/null || true

	# sleep を 1 秒単位で分割 (tmp/stop を素早く拾うため)
	local _sleep_remaining="$POLL_INTERVAL"
	while [ "${_sleep_remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && return 0
		sleep 1 || true
		_sleep_remaining=$((_sleep_remaining - 1))
	done
	return 0
}

# --- pause gate (durable operator stop via tmp/state/<name>.paused) ---
_worker_is_paused() { [ -f "tmp/state/${WORKER_NAME}.paused" ]; }
_park_while_paused() {
	# Idle quietly while the pause marker exists (no radio scheduling). Process
	# stays alive so the supervisor keeps adopting it (no respawn storm).
	# Auto-resumes when the marker is removed. Returns 1 if tmp/stop appeared.
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

while true; do
	_ensure_single_owner
	_reload_runtime
	if [ -f tmp/stop ]; then
		_log "tmp/stop 検出 → 終了"
		break
	fi

	# pause gate (durable operator stop): idle without doing work
	_park_while_paused || break

	# 1イテレーションの失敗は握りつぶし、次回に継続
	_run_iteration || _log "WARNING: iteration failed (rc=$?) — 継続"
done
