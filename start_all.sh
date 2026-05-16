#!/bin/bash
# start_all.sh - 全 worker を起動・監視する supervisor
#
# 各 worker を background で起動し、死亡時は自動再起動する。
# tmp/stop が作成されたら全 worker を停止して終了する。
#
# 起動: ./start_all.sh
# 停止: touch tmp/stop  or  ./stop_soren.sh  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- 設定 ---
MAX_RESTARTS="${SUPERVISOR_MAX_RESTARTS:-10}"
RESTART_BACKOFF_BASE="${SUPERVISOR_BACKOFF_BASE:-2}"
RESTART_BACKOFF_MAX="${SUPERVISOR_BACKOFF_MAX:-60}"
SUPERVISOR_POLL_SEC="${SUPERVISOR_POLL_SEC:-3}"
PID_FILE="tmp/state/start_all.pid"

# Worker 定義: name と command
declare -a WORKER_NAMES=(
	"soren_loop"
	"chat_worker"
	"audio_worker"
	"radio_worker"
	"prediction_worker"
	"improve_daemon"
)
declare -a WORKER_CMDS=(
	"./soren_loop.sh"
	"./workers/chat_worker.sh"
	"./workers/audio_worker.sh"
	"./workers/radio_worker.sh"
	"./workers/prediction_worker.sh"
	"./improve_daemon.sh"
)

# ランタイム状態
declare -a WORKER_PIDS=()
declare -a WORKER_RESTARTS=()
declare -a WORKER_LAST_START=()

_log() {
	echo "[supervisor $(date '+%H:%M:%S')] $*"
}

# --- 起動時クリーンアップ ---
rm -f tmp/stop
mkdir -p tmp/state logs 2>/dev/null || true

# 多重起動防止
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		_log "ERROR: supervisor 既に起動中 (PID=$old_pid)"
		exit 1
	fi
	rm -f "$PID_FILE"
fi
echo $$ > "$PID_FILE"

# --- Worker 起動 ---
_start_worker() {
	local idx="$1"
	local name="${WORKER_NAMES[$idx]}"
	local cmd="${WORKER_CMDS[$idx]}"
	local log_file="logs/${name}.log"

	if [ "$name" = "prediction_worker" ] && [ -f "tmp/state/prediction_worker.paused" ]; then
		_log "スキップ: ${name} paused"
		WORKER_PIDS[$idx]=""
		WORKER_LAST_START[$idx]=$(date +%s)
		return 0
	fi

	$cmd >>"$log_file" 2>&1 &
	local pid=$!
	WORKER_PIDS[$idx]=$pid
	WORKER_LAST_START[$idx]=$(date +%s)
	_log "起動: ${name} (PID=${pid})"
}

_stop_all_workers() {
	_log "全 worker 停止開始"
	for idx in "${!WORKER_NAMES[@]}"; do
		local pid="${WORKER_PIDS[$idx]:-}"
		local name="${WORKER_NAMES[$idx]}"
		if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
			_log "停止: ${name} (PID=${pid})"
			kill "$pid" 2>/dev/null || true
		fi
	done
	# 全 worker の終了を待つ (最大10秒)
	local deadline=$(( $(date +%s) + 10 ))
	local all_dead=false
	while [ "$(date +%s)" -lt "$deadline" ]; do
		all_dead=true
		for idx in "${!WORKER_NAMES[@]}"; do
			local pid="${WORKER_PIDS[$idx]:-}"
			if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
				all_dead=false
				break
			fi
		done
		$all_dead && break
		sleep 1
	done
	# まだ生きている worker を強制 kill
	if ! $all_dead; then
		for idx in "${!WORKER_NAMES[@]}"; do
			local pid="${WORKER_PIDS[$idx]:-}"
			local name="${WORKER_NAMES[$idx]}"
			if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
				_log "強制停止: ${name} (PID=${pid})"
				kill -9 "$pid" 2>/dev/null || true
			fi
		done
	fi
	_log "全 worker 停止完了"
}

_cleanup() {
	touch tmp/stop 2>/dev/null || true
	_stop_all_workers
	rm -f "$PID_FILE"
	_log "supervisor 終了"
}

trap '_cleanup' EXIT INT TERM

# --- 全 worker 起動 ---
_log "=== Soren Supervisor 起動 (PID=$$) ==="

for idx in "${!WORKER_NAMES[@]}"; do
	WORKER_PIDS[$idx]=""
	WORKER_RESTARTS[$idx]=0
	WORKER_LAST_START[$idx]=0
	_start_worker "$idx"
done

# --- 監視ループ ---
while true; do
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 全 worker 停止"
		break
	fi

	for idx in "${!WORKER_NAMES[@]}"; do
		_w_pid="${WORKER_PIDS[$idx]:-}"
		_w_name="${WORKER_NAMES[$idx]}"

		# worker が生きていればスキップ
		if [ -n "$_w_pid" ] && kill -0 "$_w_pid" 2>/dev/null; then
			continue
		fi

		# worker が死んだ → 再起動判定
		_w_restarts="${WORKER_RESTARTS[$idx]:-0}"
		if [ "$_w_name" = "prediction_worker" ] && [ -f "tmp/state/prediction_worker.paused" ]; then
			continue
		fi
		if [ "$_w_restarts" -ge "$MAX_RESTARTS" ]; then
			_w_last_start="${WORKER_LAST_START[$idx]:-0}"
			_w_elapsed=$(( $(date +%s) - _w_last_start ))
			if [ "$_w_elapsed" -gt 300 ]; then
				WORKER_RESTARTS[$idx]=0
				_w_restarts=0
			else
				_log "WARN: ${_w_name} が最大再起動回数 (${MAX_RESTARTS}) に到達 — スキップ"
				continue
			fi
		fi

		# exponential backoff
		_w_backoff=$(( RESTART_BACKOFF_BASE ** _w_restarts ))
		[ "$_w_backoff" -gt "$RESTART_BACKOFF_MAX" ] && _w_backoff="$RESTART_BACKOFF_MAX"
		_w_last_start="${WORKER_LAST_START[$idx]:-0}"
		_w_elapsed=$(( $(date +%s) - _w_last_start ))
		if [ "$_w_elapsed" -lt "$_w_backoff" ]; then
			continue
		fi

		_log "WARN: ${_w_name} (PID=${_w_pid}) が停止 → 再起動 (restarts=${_w_restarts})"
		WORKER_RESTARTS[$idx]=$((_w_restarts + 1))
		_start_worker "$idx"
	done

	sleep "$SUPERVISOR_POLL_SEC"
done

exit 0
