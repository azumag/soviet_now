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

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
fi

# --- 設定 ---
MAX_RESTARTS="${SUPERVISOR_MAX_RESTARTS:-10}"
RESTART_BACKOFF_BASE="${SUPERVISOR_BACKOFF_BASE:-2}"
RESTART_BACKOFF_MAX="${SUPERVISOR_BACKOFF_MAX:-60}"
SUPERVISOR_POLL_SEC="${SUPERVISOR_POLL_SEC:-3}"
PID_FILE="tmp/state/start_all.pid"
TMUX_SESSION="${SUPERVISOR_TMUX_SESSION:-soren_supervisor}"
DUPLICATE_STATE_FILE="${SUPERVISOR_DUPLICATE_STATE_FILE:-tmp/state/worker_duplicates.json}"

# Worker 定義: name と command
declare -a WORKER_NAMES=(
	"soren_loop"
	"chat_worker"
	"youtube_worker"
	"audio_worker"
	"deadline_monitor"
	"radio_worker"
	"prediction_worker"
)
declare -a WORKER_CMDS=(
	"./soren_loop.sh"
	"./workers/chat_worker.sh ${TWITCH_CHANNEL:-azumagbanjo}"
	"./workers/youtube_worker.sh"
	"./workers/audio_worker.sh"
	"./workers/deadline_monitor.sh"
	"./workers/radio_worker.sh"
	"./workers/prediction_worker.sh"
)

# The Linux broadcast VM historically launched soviet_local.mjs from a
# separate manual tmux session. Under the boot-persistent runtime, keep the
# existing bridge watchdog inside the same supervisor lifecycle so a reboot
# cannot leave only the AI loop running against a stale game_state.json.
BRIDGE_WATCHDOG_ENABLED="${SOREN_SOVIET_WATCHDOG_ENABLED:-0}"
case "$BRIDGE_WATCHDOG_ENABLED" in
0) ;;
1)
	WORKER_NAMES=("soviet_watchdog" "${WORKER_NAMES[@]}")
	WORKER_CMDS=("./soviet_watchdog.sh" "${WORKER_CMDS[@]}")
	;;
*)
	echo "SOREN_SOVIET_WATCHDOG_ENABLED must be 0 or 1" >&2
	exit 2
	;;
esac

# OBS' two HTML status surfaces used to live in detached tmux panes. On the
# boot-persistent Linux runtime, supervise their watch loops directly so every
# process belongs to the unit lifecycle and is recreated after a restart.
OVERLAY_WATCHERS_ENABLED="${SOREN_STATUS_OVERLAY_WATCHERS_ENABLED:-0}"
case "$OVERLAY_WATCHERS_ENABLED" in
0) ;;
1)
	WORKER_NAMES+=("status_overlay_watch" "show_status_overlay_watch")
	WORKER_CMDS+=("./generate_status_overlay.sh watch 2" "./generate_show_status_overlay.sh watch 2")
	;;
*)
	echo "SOREN_STATUS_OVERLAY_WATCHERS_ENABLED must be 0 or 1" >&2
	exit 2
	;;
esac

STREAM_BACKEND="${SOREN_STREAM_BACKEND:-obs}"
case "$STREAM_BACKEND" in
obs)
	WORKER_NAMES+=("obs_capture_watchdog")
	WORKER_CMDS+=("./obs_capture_watchdog.sh")
	;;
ffmpeg)
	WORKER_NAMES+=("direct_stream")
	WORKER_CMDS+=("./direct_stream.sh run")
	;;
*)
	echo "SOREN_STREAM_BACKEND must be obs or ffmpeg" >&2
	exit 2
	;;
esac

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
--print-worker-config)
	python3 - "$STREAM_BACKEND" "${WORKER_NAMES[@]}" <<'PY'
import json
import sys

print(json.dumps({"backend": sys.argv[1], "workers": sys.argv[2:]}, sort_keys=True))
PY
	exit 0
	;;
--daemon|daemon|start)
	if [ "$STREAM_BACKEND" = "ffmpeg" ] && [ "$(uname -s)" != "Linux" ]; then
		echo "SOREN_STREAM_BACKEND=ffmpeg is Linux-only" >&2
		exit 2
	fi
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
	if [ "$STREAM_BACKEND" = "ffmpeg" ] && [ "$(uname -s)" != "Linux" ]; then
		echo "SOREN_STREAM_BACKEND=ffmpeg is Linux-only" >&2
		exit 2
	fi
	;;
--help|-h)
	echo "Usage: $0 [--daemon|--supervisor|--print-worker-config]"
	exit 0
	;;
*)
	echo "Usage: $0 [--daemon|--supervisor|--print-worker-config]" >&2
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
	obs_capture_watchdog) echo "tmp/state/obs_capture_watchdog.pid" ;;
	soviet_watchdog) echo "tmp/state/.soviet_watchdog.lock/owner" ;;
	status_overlay_watch) echo "tmp/state/status_overlay_watch.pid" ;;
	show_status_overlay_watch) echo "tmp/state/show_status_overlay_watch.pid" ;;
	direct_stream) echo "tmp/state/direct_stream.pid" ;;
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
	obs_capture_watchdog) echo '[/ ]obs_capture_watchdog[.]sh([[:space:]]|$)' ;;
	soviet_watchdog) echo '[/ ]soviet_watchdog[.]sh([[:space:]]|$)' ;;
	status_overlay_watch) echo '[/ ]generate_status_overlay[.]sh[[:space:]]+watch([[:space:]]|$)' ;;
	show_status_overlay_watch) echo '[/ ]generate_show_status_overlay[.]sh[[:space:]]+watch([[:space:]]|$)' ;;
	direct_stream) echo '[/ ]lib/direct_stream[.]py[[:space:]]+run([[:space:]]|$)' ;;
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
		log_m=$(stat -f %m logs/soren_loop.log 2>/dev/null) \
			|| log_m=$(stat -c %Y logs/soren_loop.log 2>/dev/null) \
			|| log_m=0
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

_improve_daemon_responsive() {
	local pid="${1:-}" lock_file="${IMPROVE_LOCK_FILE:-tmp/improve.lock}" state_file="${IMPROVE_STATE_FILE:-tmp/state/improve_state.json}"
	local log_file="${IMPROVE_DAEMON_LOG_FILE:-logs/improve_daemon.log}" threshold now lock_m log_m state_m lock_age log_age state_age status detail
	_pid_matches_worker "$pid" "$(_pattern_for_worker improve_daemon)" || return 1
	[ -f "$lock_file" ] || return 0
	threshold="${IMPROVE_DAEMON_LOCK_STALL_SEC:-180}"
	case "$threshold" in ''|*[!0-9]*) threshold=180 ;; esac
	now=$(date +%s)
	lock_m=$(stat -f %m "$lock_file" 2>/dev/null) \
		|| lock_m=$(stat -c %Y "$lock_file" 2>/dev/null) \
		|| lock_m=0
	log_m=$(stat -f %m "$log_file" 2>/dev/null) \
		|| log_m=$(stat -c %Y "$log_file" 2>/dev/null) \
		|| log_m=0
	state_m=$(stat -f %m "$state_file" 2>/dev/null) \
		|| state_m=$(stat -c %Y "$state_file" 2>/dev/null) \
		|| state_m=0
	case "$lock_m" in ''|*[!0-9]*) lock_m=0 ;; esac
	case "$log_m" in ''|*[!0-9]*) log_m=0 ;; esac
	case "$state_m" in ''|*[!0-9]*) state_m=0 ;; esac
	lock_age=$((now - lock_m))
	log_age=$((now - log_m))
	state_age=$((now - state_m))
	status=$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", ""))
except Exception:
    print("")' "$state_file" 2>/dev/null || echo "")
	if [ "$lock_age" -ge "$threshold" ] && [ "$log_age" -ge "$threshold" ] && [ "$state_age" -ge "$threshold" ] && { [ -z "$status" ] || [ "$status" = "idle" ]; }; then
		detail="lock_age=${lock_age}s log_age=${log_age}s state_age=${state_age}s status=${status:-unknown}"
		_log "WARN: improve_daemon PID=${pid} は改善ロックを消費していない (${detail}) → 再起動"
		mkdir -p tmp/state 2>/dev/null || true
		printf '{"status":"stalled","updated_at":%s,"pid":%s,"detail":"%s"}\n' "$now" "$pid" "$detail" >"tmp/state/improve_daemon_stall.json" 2>/dev/null || true
		kill -TERM "$pid" 2>/dev/null || true
		return 1
	fi
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
			if [ "$name" = "improve_daemon" ] && ! _improve_daemon_responsive "$pid"; then
				rm -f "$pid_file" 2>/dev/null || true
				return 1
			fi
			echo "$pid"
			return 0
		fi
		rm -f "$pid_file" 2>/dev/null || true
	fi
	# The bridge watchdog owns a singleton lock and records its PID there.  Do
	# not fall back to pgrep for this worker: a deployment/SSH command whose
	# arguments merely mention soviet_watchdog.sh can otherwise be mistaken for
	# the worker and adopted until that short-lived shell exits.
	case "$name" in
	soviet_watchdog|status_overlay_watch|show_status_overlay_watch)
		return 1
		;;
	esac
	if [ -n "$pattern" ]; then
		pid=$(pgrep -f "$pattern" 2>/dev/null | head -n 1 || true)
		if _pid_alive "$pid"; then
			if [ "$name" = "soren_loop" ] && ! _soren_loop_adoptable "$pid"; then
				return 1
			fi
			if [ "$name" = "improve_daemon" ] && ! _improve_daemon_responsive "$pid"; then
				return 1
			fi
			echo "$pid"
			return 0
		fi
	fi
	return 1
}

_soren_loop_lock_pid() {
	local pid=""
	pid=$(cat "tmp/.soren_loop.lock/pid" 2>/dev/null || true)
	if _pid_matches_worker "$pid" "$(_pattern_for_worker soren_loop)"; then
		echo "$pid"
		return 0
	fi
	return 1
}

_detect_worker_duplicates() {
	local managed="" sep="" idx name pid signature="" ps_snapshot_file=""
	for idx in "${!WORKER_NAMES[@]}"; do
		name="${WORKER_NAMES[$idx]}"
		pid="${WORKER_PIDS[$idx]:-}"
		managed="${managed}${sep}${name}:${pid}"
		sep=","
	done
	ps_snapshot_file="tmp/state/worker_duplicates.ps.$$.txt"
	LC_ALL=C ps -Ao pid=,ppid=,command= >"$ps_snapshot_file" 2>/dev/null || : >"$ps_snapshot_file"
	signature=$(python3 - "$managed" "$DUPLICATE_STATE_FILE" "$ps_snapshot_file" <<'PY' 2>/dev/null || true
import json
import os
import re
import subprocess
import sys
import time

managed_arg = sys.argv[1] if len(sys.argv) > 1 else ""
out_file = sys.argv[2] if len(sys.argv) > 2 else "tmp/state/worker_duplicates.json"
ps_snapshot_file = sys.argv[3] if len(sys.argv) > 3 else ""
managed = {}
for item in managed_arg.split(","):
    if ":" not in item:
        continue
    name, pid = item.split(":", 1)
    managed[name] = pid

patterns = {
    "soren_loop": r"[/ ]soren_loop[.]sh([ \t]|$)",
    "chat_worker": r"[/ ]workers/chat_worker[.]sh([ \t]|$)",
    "youtube_worker": r"[/ ]workers/youtube_worker[.]sh([ \t]|$)",
    "audio_worker": r"[/ ]workers/audio_worker[.]sh([ \t]|$)",
    "deadline_monitor": r"[/ ]workers/deadline_monitor[.]sh([ \t]|$)|[/ ]deadline_misplacement_monitor[.]py([ \t]|$)",
    "radio_worker": r"[/ ]workers/radio_worker[.]sh([ \t]|$)",
    "prediction_worker": r"[/ ]workers/prediction_worker[.]sh([ \t]|$)",
    "improve_daemon": r"[/ ]improve_daemon[.]sh([ \t]|$)",
    "soviet_watchdog": r"[/ ]soviet_watchdog[.]sh([ \t]|$)",
    "status_overlay_watch": r"[/ ]generate_status_overlay[.]sh[ \t]+watch([ \t]|$)",
    "show_status_overlay_watch": r"[/ ]generate_show_status_overlay[.]sh[ \t]+watch([ \t]|$)",
    "direct_stream": r"[/ ]lib/direct_stream[.]py[ \t]+run([ \t]|$)",
}

try:
    raw = ""
    if ps_snapshot_file and os.path.exists(ps_snapshot_file):
        with open(ps_snapshot_file, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    if not raw:
        raw = subprocess.check_output(["ps", "-Ao", "pid=,ppid=,command="], text=True, errors="replace")
except Exception as exc:
    state = {
        "status": "unknown",
        "updated_at": int(time.time()),
        "error": f"ps_failed:{type(exc).__name__}",
        "duplicates": [],
    }
else:
    duplicates = []
    all_counts = {}
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append((parts[0], parts[1], parts[2]))

    def root_worker_pids(parsed_rows):
        roots = {}
        for name, pattern in patterns.items():
            rx = re.compile(pattern)
            matched = []
            for pid, ppid, cmd in parsed_rows:
                if pid == str(os.getpid()):
                    continue
                if rx.search(cmd):
                    matched.append((pid, ppid))
            matched_pids = {pid for pid, _ppid in matched}
            # Worker shell loops often spawn a same-command child shell. Count only
            # root instances so normal child shells do not look like duplicates.
            roots[name] = sorted(
                {pid for pid, ppid in matched if ppid not in matched_pids},
                key=lambda p: int(p),
            )
        return roots

    first_roots = root_worker_pids(rows)
    second_roots = first_roots
    try:
        time.sleep(0.35)
        raw2 = subprocess.check_output(["ps", "-Ao", "pid=,ppid=,command="], text=True, errors="replace")
        rows2 = []
        for line in raw2.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows2.append((parts[0], parts[1], parts[2]))
        second_roots = root_worker_pids(rows2)
    except Exception:
        pass

    for name in patterns:
        unique_pids = sorted(
            set(first_roots.get(name, [])) & set(second_roots.get(name, [])),
            key=lambda p: int(p),
        )
        all_counts[name] = len(unique_pids)
        if len(unique_pids) > 1:
            owner = managed.get(name, "")
            extras = [p for p in unique_pids if p != owner]
            duplicates.append({
                "name": name,
                "count": len(unique_pids),
                "managed_pid": owner,
                "pids": unique_pids,
                "extra_pids": extras,
            })
    state = {
        "status": "duplicate" if duplicates else "ok",
        "updated_at": int(time.time()),
        "managed": managed,
        "counts": all_counts,
        "duplicates": duplicates,
    }

os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
tmp = out_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, sort_keys=True)
os.replace(tmp, out_file)

if state["status"] == "duplicate":
    print(";".join(
        f"{item['name']}={','.join(item['pids'])}"
        for item in state["duplicates"]
    ))
PY
)
	rm -f "$ps_snapshot_file" 2>/dev/null || true
	if [ -n "$signature" ] && [ "$signature" != "${_SUPERVISOR_DUP_LAST_SIGNATURE:-}" ]; then
		_SUPERVISOR_DUP_LAST_SIGNATURE="$signature"
		_log "WARN: worker duplicate detected: ${signature}"
	fi
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

# Worker pause gate: while tmp/state/<name>.paused exists, the worker is not
# started and not respawned (durable operator-level stop). Generalizes the
# previous prediction_worker-specific pause so any worker can be held down.
_worker_paused() {
	local name="$1"
	[ -n "$name" ] && [ -f "tmp/state/${name}.paused" ]
}

# --- Worker 起動 ---
_start_worker() {
	local idx="$1"
	local name="${WORKER_NAMES[$idx]}"
	local cmd="${WORKER_CMDS[$idx]}"
	local log_file="logs/${name}.log"
	local existing_pid=""

	if _worker_paused "$name"; then
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
	if [ "$name" = "soren_loop" ]; then
		sleep 1
		if lock_pid="$(_soren_loop_lock_pid)"; then
			pid="$lock_pid"
		fi
	fi
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
	# INT/TERM is the normal systemd stop path. Returning success prevents a
	# deliberate restart or shutdown from being recorded as a unit failure.
	exit 0
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
_detect_worker_duplicates

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
			if [ "$_w_name" = "improve_daemon" ] && ! _improve_daemon_responsive "$_w_pid"; then
				WORKER_PIDS[$idx]=""
				_w_pid=""
			else
			_w_pid_file="$(_pidfile_for_worker "$_w_name")"
			if [ "$_w_name" != "soren_loop" ] && [ -n "$_w_pid_file" ] && [ -n "$_w_pid" ]; then
				_w_recorded_pid=$(cat "$_w_pid_file" 2>/dev/null || true)
				if [ "$_w_recorded_pid" != "$_w_pid" ]; then
					mkdir -p "$(dirname "$_w_pid_file")" 2>/dev/null || true
					echo "$_w_pid" >"$_w_pid_file" 2>/dev/null || true
				fi
			fi
			continue
			fi
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
		if _worker_paused "$_w_name"; then
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
	_detect_worker_duplicates

	sleep "$SUPERVISOR_POLL_SEC"
done

exit 0
