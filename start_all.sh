#!/bin/bash
# start_all.sh - 全 worker を起動・監視する supervisor
#
# 各 worker を background で起動し、死亡時は自動再起動する。
# tmp/stop が作成されたら全 worker を停止して終了する。
#
# 起動: ./start_all.sh --daemon  (推奨: shell 終了後も supervisor を残す)
#       ./start_all.sh           (foreground で監視)
# 停止: touch tmp/stop  or  ./stop_soren.sh  or  Ctrl+C

set -o pipefail
# Supervisor is often launched from automation or a short-lived shell.  Keep it
# alive when that parent shell exits; explicit stop still goes through tmp/stop
# or INT/TERM.
trap '' HUP
trap ':' PIPE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

# --- 設定 ---
MAX_RESTARTS="${SUPERVISOR_MAX_RESTARTS:-10}"
RESTART_BACKOFF_BASE="${SUPERVISOR_BACKOFF_BASE:-2}"
RESTART_BACKOFF_MAX="${SUPERVISOR_BACKOFF_MAX:-60}"
SUPERVISOR_POLL_SEC="${SUPERVISOR_POLL_SEC:-3}"
PID_FILE="tmp/state/start_all.pid"
TMUX_SESSION="${SUPERVISOR_TMUX_SESSION:-soren_supervisor}"

# Worker 定義: name と command
declare -a WORKER_NAMES=(
	"soren_loop"
	"chat_worker"
	"youtube_worker"
	"audio_worker"
	"deadline_monitor"
	"radio_worker"
	"prediction_worker"
	"improve_daemon"
)
declare -a WORKER_CMDS=(
	"./soren_loop.sh"
	"./workers/chat_worker.sh"
	"./workers/youtube_worker.sh"
	"./workers/audio_worker.sh"
	"./workers/deadline_monitor.sh"
	"./workers/radio_worker.sh"
	"./workers/prediction_worker.sh"
	"./improve_daemon.sh"
)

# ランタイム状態
declare -a WORKER_PIDS=()
declare -a WORKER_RESTARTS=()
declare -a WORKER_LAST_START=()
SUPERVISOR_STOP_REQUESTED=0

_log() {
	echo "[supervisor $(date '+%H:%M:%S')] $*"
}

_pid_alive() {
	local pid="${1:-}"
	local err=""
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in
	*"operation not permitted"*|*"Operation not permitted"*)
		# Codex/macOS sandboxed checks can see a live user-session worker PID
		# but be denied signal permission. Treat that as alive so the
		# supervisor does not overwrite pidfiles or start duplicate workers.
		return 0
		;;
	esac
	return 1
}

case "${1:-}" in
--daemon|daemon|start)
	mkdir -p tmp/state logs 2>/dev/null || true
	if [ -f "$PID_FILE" ]; then
		old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
		if _pid_alive "$old_pid"; then
			echo "supervisor already running (PID=$old_pid)"
			exit 0
		fi
		rm -f "$PID_FILE"
	fi
	rm -f tmp/stop
	if command -v tmux >/dev/null 2>&1; then
		if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
			old_pid=$(tmux display-message -p -t "$TMUX_SESSION" "#{pane_pid}" 2>/dev/null || true)
			if _pid_alive "$old_pid"; then
				echo "$old_pid" >"$PID_FILE"
				echo "supervisor already running in tmux (PID=$old_pid)"
				exit 0
			fi
			tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
		fi
		tmux new-session -d -s "$TMUX_SESSION" "cd '$PWD' && exec /bin/bash ./start_all.sh --supervisor >> logs/start_all.log 2>&1"
		sleep 1
		if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
			new_pid=$(tmux display-message -p -t "$TMUX_SESSION" "#{pane_pid}" 2>/dev/null || true)
			echo "$new_pid" >"$PID_FILE"
			echo "supervisor tmux daemon started (PID=$new_pid)"
			exit 0
		fi
		echo "tmux-start-failed:fallback-nohup" >>"logs/start_all.log"
	fi
	nohup /bin/bash "$0" --supervisor >>"logs/start_all.log" 2>&1 </dev/null &
	new_pid=$!
	echo "$new_pid" >"$PID_FILE"
	disown "$new_pid" 2>/dev/null || true
	echo "supervisor daemon started (PID=$new_pid)"
	exit 0
	;;
--supervisor|"")
	;;
--help|-h)
	echo "Usage: $0 [--daemon|--supervisor]"
	exit 0
	;;
*)
	echo "Usage: $0 [--daemon|--supervisor]" >&2
	exit 2
	;;
esac

_pidfile_for_worker() {
	case "$1" in
	soren_loop) echo "tmp/.soren_loop.lock/pid" ;;
	chat_worker) echo "tmp/state/chat_worker.pid" ;;
	youtube_worker) echo "tmp/state/youtube_worker.pid" ;;
	audio_worker) echo "tmp/state/audio_worker.pid" ;;
	deadline_monitor) echo "tmp/state/deadline_monitor.pid" ;;
	radio_worker) echo "tmp/state/radio_worker.pid" ;;
	prediction_worker) echo "tmp/state/prediction_worker.pid" ;;
	improve_daemon) echo "${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}" ;;
	*) echo "" ;;
	esac
}

_pattern_for_worker() {
	case "$1" in
	soren_loop) echo '[/ ]soren_loop[.]sh([[:space:]]|$)' ;;
	chat_worker) echo '[/ ]workers/chat_worker[.]sh([[:space:]]|$)' ;;
	youtube_worker) echo '[/ ]workers/youtube_worker[.]sh([[:space:]]|$)' ;;
	audio_worker) echo '[/ ]workers/audio_worker[.]sh([[:space:]]|$)' ;;
	deadline_monitor) echo '[/ ]workers/deadline_monitor[.]sh([[:space:]]|$)|[/ ]deadline_misplacement_monitor[.]py([[:space:]]|$)' ;;
	radio_worker) echo '[/ ]workers/radio_worker[.]sh([[:space:]]|$)' ;;
	prediction_worker) echo '[/ ]workers/prediction_worker[.]sh([[:space:]]|$)' ;;
	improve_daemon) echo '[/ ]improve_daemon[.]sh([[:space:]]|$)' ;;
	*) echo "" ;;
	esac
}

_pid_matches_worker() {
	local pid="${1:-}"
	local pattern="${2:-}"
	_pid_alive "$pid" || return 1
	[ -n "$pattern" ] || return 0
	if matches=$(pgrep -f "$pattern" 2>/dev/null); then
		printf '%s\n' "$matches" | grep -qx "$pid"
		return $?
	fi
	# macOS privacy/sandboxing can deny process-list access even when kill -0 works.
	# In that case, trust the alive pidfile pid instead of triggering a restart storm.
	return 0
}

_soren_loop_adoptable() {
	local pid="${1:-}" state="" log_m=0 now=0 age=0
	_pid_matches_worker "$pid" "$(_pattern_for_worker soren_loop)" || return 1
	state=$(python3 -c 'import json
try:
    print(json.load(open("game_state.json", encoding="utf-8")).get("state", ""))
except Exception:
    print("")' 2>/dev/null || echo "")
	case "$state" in
	STOP|GAMEOVER)
		log_m=$(stat -f %m logs/soren_loop.log 2>/dev/null || stat -c %Y logs/soren_loop.log 2>/dev/null || echo 0)
		now=$(date +%s)
		case "$log_m" in ''|*[!0-9]*) log_m=0 ;; esac
		age=$((now - log_m))
		if [ "$log_m" -gt 0 ] && [ "$age" -gt "${SOREN_LOOP_STOP_ADOPT_MAX_LOG_AGE:-30}" ]; then
			_log "WARN: soren_loop PID=${pid} は state=${state} かつ log stale ${age}s → 採用せず再起動"
			kill -TERM "$pid" 2>/dev/null || true
			return 1
		fi
		;;
	esac
	return 0
}

_find_existing_worker_pid() {
	local name="$1"
	local pid_file pid pattern pid
	pid_file="$(_pidfile_for_worker "$name")"
	pattern="$(_pattern_for_worker "$name")"
	if [ -n "$pid_file" ] && [ -f "$pid_file" ]; then
		pid=$(cat "$pid_file" 2>/dev/null || true)
		if _pid_matches_worker "$pid" "$pattern"; then
			if [ "$name" = "soren_loop" ] && ! _soren_loop_adoptable "$pid"; then
				rm -f "$pid_file" 2>/dev/null || true
				return 1
			fi
			echo "$pid"
			return 0
		fi
		rm -f "$pid_file" 2>/dev/null || true
	fi
	if [ -n "$pattern" ]; then
		pid=$(pgrep -f "$pattern" 2>/dev/null | head -n 1 || true)
		if _pid_alive "$pid"; then
			if [ "$name" = "soren_loop" ] && ! _soren_loop_adoptable "$pid"; then
				return 1
			fi
			echo "$pid"
			return 0
		fi
	fi
	return 1
}

# --- 起動時クリーンアップ ---
rm -f tmp/stop
mkdir -p tmp/state logs 2>/dev/null || true

# 多重起動防止
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$old_pid" ] && [ "$old_pid" != "$$" ] && _pid_alive "$old_pid"; then
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
	local existing_pid=""

	if [ "$name" = "prediction_worker" ] && [ -f "tmp/state/prediction_worker.paused" ]; then
		_log "スキップ: ${name} paused"
		WORKER_PIDS[$idx]=""
		WORKER_LAST_START[$idx]=$(date +%s)
		return 0
	fi
	if [ "$name" = "youtube_worker" ] && [ "${YOUTUBE_CHAT_ENABLED:-0}" != "1" ]; then
		_log "スキップ: ${name} disabled"
		WORKER_PIDS[$idx]=""
		WORKER_LAST_START[$idx]=$(date +%s)
		return 0
	fi
	if existing_pid="$(_find_existing_worker_pid "$name")"; then
		WORKER_PIDS[$idx]="$existing_pid"
		WORKER_LAST_START[$idx]=$(date +%s)
		_log "採用: ${name} (PID=${existing_pid})"
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
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ -n "$active_pid" ] && [ "$active_pid" != "$$" ]; then
		_log "cleanup skipped: another supervisor owns pidfile (owner=${active_pid}, self=$$)"
		return 0
	fi
	if [ "${SUPERVISOR_STOP_REQUESTED:-0}" = "1" ]; then
		touch tmp/stop 2>/dev/null || true
		_stop_all_workers
	else
		_log "unexpected supervisor exit; leaving workers alive"
	fi
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ -z "$active_pid" ] || [ "$active_pid" = "$$" ]; then
		rm -f "$PID_FILE"
	fi
	_log "supervisor 終了"
}

_handle_supervisor_signal() {
	SUPERVISOR_STOP_REQUESTED=1
	_cleanup
	trap - EXIT
	exit 130
}

trap '_cleanup' EXIT
trap '_handle_supervisor_signal' INT TERM

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
		SUPERVISOR_STOP_REQUESTED=1
		_log "stop ファイル検出 → 全 worker 停止"
		break
	fi

	for idx in "${!WORKER_NAMES[@]}"; do
		_w_pid="${WORKER_PIDS[$idx]:-}"
		_w_name="${WORKER_NAMES[$idx]}"
		_w_pattern="$(_pattern_for_worker "$_w_name")"

		# worker が生きていればスキップ
		if _pid_matches_worker "$_w_pid" "$_w_pattern"; then
			_w_pid_file="$(_pidfile_for_worker "$_w_name")"
			if [ -n "$_w_pid_file" ] && [ -n "$_w_pid" ]; then
				_w_recorded_pid=$(cat "$_w_pid_file" 2>/dev/null || true)
				if [ "$_w_recorded_pid" != "$_w_pid" ]; then
					mkdir -p "$(dirname "$_w_pid_file")" 2>/dev/null || true
					echo "$_w_pid" >"$_w_pid_file" 2>/dev/null || true
				fi
			fi
			continue
		fi

		# supervisor の内部 PID が古くなっても、実 worker が生きていれば採用して監視を継続する。
		# これを最大再起動判定より前に置き、post-restart などで外側 PID が入れ替わった状態から復旧する。
		if existing_pid="$(_find_existing_worker_pid "$_w_name")"; then
			WORKER_PIDS[$idx]="$existing_pid"
			WORKER_RESTARTS[$idx]=0
			WORKER_LAST_START[$idx]=$(date +%s)
			_log "採用: ${_w_name} (PID=${existing_pid}) after stale supervisor pid"
			continue
		fi

		# worker が死んだ → 再起動判定
		_w_restarts="${WORKER_RESTARTS[$idx]:-0}"
		if [ "$_w_name" = "prediction_worker" ] && [ -f "tmp/state/prediction_worker.paused" ]; then
			continue
		fi
		if [ "$_w_name" = "youtube_worker" ] && [ "${YOUTUBE_CHAT_ENABLED:-0}" != "1" ]; then
			continue
		fi
		if [ "$_w_name" = "soren_loop" ] && [ -f "${IMPROVE_LOCK_FILE:-tmp/improve.lock}" ]; then
			WORKER_LAST_START[$idx]=$(date +%s)
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
