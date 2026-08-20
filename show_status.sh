#!/bin/zsh
# show_status.sh - eloop 全体のステータス表示
#
# Usage: ./show_status.sh        # 10秒間隔で常時表示
#        ./show_status.sh 3      # 3秒間隔で常時表示
#        ./show_status.sh --once # 1回だけ表示して終了（確認・自動監視用）
#        ./show_status.sh --html-once
#        ./show_status.sh --html-watch [sec]
#        ./show_status.sh --html-start [sec]
#        ./show_status.sh --html-stop
#        ./show_status.sh --html-obs [show|hide]

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

_is_wildcard_parallel_active() {
	# 2026-05-31 fix (OBS overlay flicker): improve_state.json routinely goes idle while
	# wildcard_parallel.py is still running (orphaned), so checking ONLY it returns false during
	# a live param-parallel — then --html-obs re-SHOWS ops/stats overlays and they FLICKER (~15s)
	# against the param-parallel reconcile that hides them. So ALSO treat a FRESH
	# wildcard_parallel_status.json (phase generating/running, mtime <= 180s so a crashed/stale
	# run releases the overlays) as active.
	local _wp="${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}"
	if [ -f "$_wp" ] && python3 - "$_wp" <<'PY' >/dev/null 2>&1
import json, os, sys, time
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
try:
    age = time.time() - os.path.getmtime(sys.argv[1])
except Exception:
    age = 1e9
try:
    started_at = float(d.get("started_at") or 0)
except Exception:
    started_at = 0
try:
    pidless_stale_sec = float(os.environ.get("WILDCARD_PARALLEL_PIDLESS_STALE_SEC", "600") or "600")
except Exception:
    pidless_stale_sec = 600
phase = str(d.get("phase") or "")
if phase in ("generating", "running") and not d.get("controller_pid"):
    if pidless_stale_sec > 0 and started_at > 0 and (time.time() - started_at) > pidless_stale_sec:
        raise SystemExit(1)
raise SystemExit(0 if phase in ("generating", "running") and age <= 600 else 1)
PY
	then
		return 0
	fi
	# Process-liveness fallback: if wildcard_parallel.py is running, treat as active
	# regardless of status file mtime (covers long games where file doesn't update for >3min)
	pgrep -f 'python.* wildcard_parallel\.py' >/dev/null 2>&1 && return 0
	[ -f tmp/state/improve_state.json ] || return 1
	python3 - tmp/state/improve_state.json <<'PY' >/dev/null 2>&1
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
active = data.get("status") == "running" and data.get("phase") == "wildcard_parallel"
raise SystemExit(0 if active else 1)
PY
}

case "${1:-}" in
--html-once)
	exec ./generate_show_status_overlay.sh once
	;;
--html-watch)
	exec ./generate_show_status_overlay.sh watch "${2:-2}"
	;;
--html-start)
	exec ./generate_show_status_overlay.sh start "${2:-2}"
	;;
--html-stop)
	exec ./generate_show_status_overlay.sh stop
	;;
	--html-obs)
		# opsOverlay is a persistent monitoring surface. Older running
		# supervisors may still ask for "hide"; keep this hot path visible.
		if _is_wildcard_parallel_active; then
			exec ./generate_show_status_overlay.sh ensure-obs hide
		fi
		exec ./generate_show_status_overlay.sh ensure-obs show
		;;
esac

SHOW_STATUS_ONCE=0
case "${1:-}" in
--once|once)
	SHOW_STATUS_ONCE=1
	WATCH_INTERVAL=10
	;;
*)
	WATCH_INTERVAL=${1:-10}
	;;
esac
DROP_REFRESH_INTERVAL=${SHOW_STATUS_DROP_REFRESH_INTERVAL:-0.25}
SHOW_STATUS_NO_FLICKER=${SHOW_STATUS_NO_FLICKER:-1}
if [[ "$SHOW_STATUS_NO_FLICKER" == "1" && -z "${FULLSCREEN_ENABLED+x}" ]]; then
	FULLSCREEN_ENABLED=0
else
	FULLSCREEN_ENABLED=${FULLSCREEN_ENABLED:-1}
fi
FULLSCREEN_RARE_N=${FULLSCREEN_RARE_N:-30}
FULLSCREEN_MIN_GAP_SEC=${FULLSCREEN_MIN_GAP_SEC:-180}
TMP_STATE_DIR="tmp/state"
TMP_MARKERS_DIR="tmp/markers"
TMP_HISTORY_DIR="tmp/history"
TMP_DEBUG_DIR="tmp/debug"
LATEST_DROP_LOG="game_history/latest.jsonl"
CURRENT_STRATEGY_RUN_FILE="$TMP_STATE_DIR/current_strategy_run.json"
ACTIVE_BRANCH_FILE="$TMP_STATE_DIR/active_branch.json"
FULLSCREEN_LAST_FILE="$TMP_STATE_DIR/.status_fullscreen_last"

_config_int_default() {
	local name="$1" fallback="$2" value=""
	value=$(sed -nE "s/^${name}=\\\"?([0-9]+)\\\"?.*/\\1/p" core/config.sh 2>/dev/null | tail -n 1)
	case "$value" in
	''|*[!0-9]*) echo "$fallback" ;;
	*) echo "$value" ;;
	esac
}

_env_config_value_default() {
	local name="$1" fallback="$2" value=""
	value=$(sed -nE "s/^${name}=['\\\"]?([^#'\\\"]+)['\\\"]?.*/\\1/p" .env 2>/dev/null | tail -n 1)
	value="${value%%[[:space:]]#*}"
	value="${value%"${value##*[![:space:]]}"}"
	if [[ -z "$value" ]]; then
		value=$(sed -nE "s/^${name}=.*:-([^}]+).*/\\1/p" core/config.sh 2>/dev/null | tail -n 1)
		value="${value%%[[:space:]]*}"
	fi
	[[ -n "$value" ]] && echo "$value" || echo "$fallback"
}

MIN_GAMES_BEFORE_IMPROVE_ENV="${MIN_GAMES_BEFORE_IMPROVE:-}"
MIN_GAMES_BEFORE_IMPROVE=${MIN_GAMES_BEFORE_IMPROVE:-$(_config_int_default MIN_GAMES_BEFORE_IMPROVE 12)}
YOUTUBE_CHAT_ENABLED=${YOUTUBE_CHAT_ENABLED:-$(_env_config_value_default YOUTUBE_CHAT_ENABLED 0)}
STREAM_BACKEND=${SOREN_STREAM_BACKEND:-$(_env_config_value_default SOREN_STREAM_BACKEND obs)}
STREAM_BACKEND=${STREAM_BACKEND:l}
DIRECT_SOAK_STATE_DIR=${SOREN_DIRECT_SOAK_STATE_DIR:-tmp/state/direct_soak}
DIRECT_AV_SYNC_STATE_FILE=${SOREN_DIRECT_AV_SYNC_STATE_FILE:-tmp/state/direct_av_sync_probe.json}
MIN_GAMES_BEFORE_REGRESSION=${MIN_GAMES_BEFORE_REGRESSION:-12}
MIN_GAMES_FOR_BEST_ROLLBACK=${MIN_GAMES_FOR_BEST_ROLLBACK:-12}
REGRESSION_MAX_RANK=${REGRESSION_MAX_RANK:-20}
REGRESSION_COMPOSITE_RATIO=${REGRESSION_COMPOSITE_RATIO:-0.88}
REGRESSION_P50_RATIO=${REGRESSION_P50_RATIO:-0.85}
REGRESSION_P25_RATIO=${REGRESSION_P25_RATIO:-0.80}
REGRESSION_MIN_COMP_GAP=${REGRESSION_MIN_COMP_GAP:-1200}
REGRESSION_MIN_P50_GAP=${REGRESSION_MIN_P50_GAP:-1000}
REGRESSION_MIN_P25_GAP=${REGRESSION_MIN_P25_GAP:-1800}
REGRESSION_MIN_BREACH_COUNT=${REGRESSION_MIN_BREACH_COUNT:-2}
BRANCH_MAX_DEPTH=${BRANCH_MAX_DEPTH:-4}
BRANCH_MAX_GAMES=${BRANCH_MAX_GAMES:-48}
BRANCH_PATIENCE=${BRANCH_PATIENCE:-3}
BRANCH_HARD_COMP_GAP=${BRANCH_HARD_COMP_GAP:-2200}
BRANCH_HARD_P50_GAP=${BRANCH_HARD_P50_GAP:-1800}
BRANCH_HARD_P25_GAP=${BRANCH_HARD_P25_GAP:-2600}
BRANCH_HARD_MIN_BREACH_COUNT=${BRANCH_HARD_MIN_BREACH_COUNT:-2}
REJECTED_REEVALUATE_TTL_SEC=${REJECTED_REEVALUATE_TTL_SEC:-21600}
RADIO_STATE_STALE_SEC=${RADIO_STATE_STALE_SEC:-600}
SHOW_STATUS_ROLLBACK_HISTORY_LIMIT=${SHOW_STATUS_ROLLBACK_HISTORY_LIMIT:-5}
DIVERSITY_PREMIUM_ENABLED=${DIVERSITY_PREMIUM_ENABLED:-$(_env_config_value_default DIVERSITY_PREMIUM_ENABLED 0)}
TABU_ENABLED=${TABU_ENABLED:-$(_env_config_value_default TABU_ENABLED 0)}
WILDCARD_ENABLED=${WILDCARD_ENABLED:-$(_env_config_value_default WILDCARD_ENABLED 0)}
WILDCARD_TRIGGER_STAGNATION=${WILDCARD_TRIGGER_STAGNATION:-$(_env_config_value_default WILDCARD_TRIGGER_STAGNATION 3)}
WILDCARD_REGRESSION_STREAK=${WILDCARD_REGRESSION_STREAK:-$(_env_config_value_default WILDCARD_REGRESSION_STREAK 2)}

case "$WATCH_INTERVAL" in
''|*[!0-9]*) WATCH_INTERVAL=10 ;;
esac
[[ "$DROP_REFRESH_INTERVAL" =~ '^[0-9]+([.][0-9]+)?$' ]] || DROP_REFRESH_INTERVAL=0.25
(( WATCH_INTERVAL < 1 )) && WATCH_INTERVAL=10
(( DROP_REFRESH_INTERVAL <= 0 )) && DROP_REFRESH_INTERVAL=0.25
(( DROP_REFRESH_INTERVAL > WATCH_INTERVAL )) && DROP_REFRESH_INTERVAL=$WATCH_INTERVAL
case "$SHOW_STATUS_ROLLBACK_HISTORY_LIMIT" in
''|*[!0-9]*) SHOW_STATUS_ROLLBACK_HISTORY_LIMIT=5 ;;
esac
(( SHOW_STATUS_ROLLBACK_HISTORY_LIMIT < 1 )) && SHOW_STATUS_ROLLBACK_HISTORY_LIMIT=1

#=== レイアウト幅 (タイトル罫線に合わせる) ===
W=57

#=== 色定義 ===
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_DIM='\033[2m'
C_GREEN='\033[32m'
C_YELLOW='\033[33m'
C_RED='\033[31m'
C_CYAN='\033[36m'
C_MAGENTA='\033[35m'
C_WHITE='\033[97m'
C_BLUE='\033[34m'

#=== ヘルパー ===

_pid_exists() {
	local pid="$1" err=""
	case "$pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	# In the Codex/macOS sandbox, stale or reused PIDs can surface as
	# "operation not permitted". Treat that as unknown/dead for project worker
	# status so pidfiles cannot make stopped workers look healthy.
	return 1
}

_file_recent() {
	local file_path="$1" max_age="${2:-0}" mtime=0 now=0 age=999999
	case "$max_age" in
	''|*[!0-9]*) return 1 ;;
	esac
	[ -f "$file_path" ] || return 1
	mtime=$(stat -f %m "$file_path" 2>/dev/null) \
		|| mtime=$(stat -c %Y "$file_path" 2>/dev/null) \
		|| mtime=0
	case "$mtime" in
	''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	age=$(( now - mtime ))
	(( age >= 0 && age <= max_age ))
}

_ai_backoff_status_lines() {
	# ai_generate.sh writes these files only for explicit provider rate limits.
	# Keep status rendering read-only; expiry cleanup remains the dispatcher's job.
	AI_BACKOFF_DIR="${AI_BACKOFF_DIR:-$TMP_STATE_DIR/ai_backoff}" \
		python3 lib/ai_backoff_status.py --lines 2>/dev/null || true
}

_obs_status_line() {
	python3 - <<'PY' 2>/dev/null || printf 'unknown|status unavailable'
import os
import socket
from pathlib import Path

env = {}
try:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        env.setdefault(k.strip(), v)
except Exception:
    pass

port_raw = os.environ.get("OBS_WEBSOCKET_PORT") or env.get("OBS_WEBSOCKET_PORT") or "4455"
try:
    port = int(float(port_raw))
except Exception:
    port = 4455
host = os.environ.get("OBS_WEBSOCKET_HOST") or env.get("OBS_WEBSOCKET_HOST") or "127.0.0.1"

try:
    with socket.create_connection((host, port), timeout=0.25):
        print(f"ok|ws {host}:{port}")
        raise SystemExit(0)
except Exception:
    pass

obs_dir = Path.home() / "Library" / "Application Support" / "obs-studio"
safe_file = obs_dir / "safe_mode"
latest = None
try:
    logs = sorted((obs_dir / "logs").glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = logs[0] if logs else None
except Exception:
    latest = None

safe_prompt = False
if latest:
    try:
        text = latest.read_text(encoding="utf-8", errors="replace")[:8192]
        safe_prompt = "[Safe Mode] Unclean shutdown detected!" in text
    except Exception:
        safe_prompt = False

if safe_file.exists() or safe_prompt:
    detail = "SafeMode prompt" if safe_prompt else "safe_mode flag"
    print(f"safe|{detail}")
else:
    print(f"down|ws {host}:{port} closed")
PY
}

_direct_stream_status_line() {
	local raw=""
	if [[ ! -x ./direct_stream.sh ]]; then
		printf 'unknown|direct_stream.sh missing'
		return 0
	fi
	raw=$(./direct_stream.sh status 2>/dev/null || true)
	DIRECT_STREAM_STATUS_RAW="$raw" python3 - <<'PY' 2>/dev/null || printf 'unknown|status unavailable'
import json
import os

try:
    state = json.loads(os.environ.get("DIRECT_STREAM_STATUS_RAW", ""))
except Exception:
    print("unknown|invalid status JSON")
    raise SystemExit(0)

if state.get("running") is True:
    fps = state.get("fps", 0)
    speed = state.get("speed", 0)
    drop = state.get("drop_frames", 0)
    dup = state.get("dup_frames", 0)
    print(f"ok|fps={fps} speed={speed} drop={drop} dup={dup}")
else:
    print(f"down|state={state.get('state', 'not_started')}")
PY
}

_direct_soak_status_line() {
	python3 - "$DIRECT_SOAK_STATE_DIR/status.json" <<'PY' 2>/dev/null || printf 'unknown|status unavailable'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    state = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("not_started|not started")
    raise SystemExit

name = str(state.get("state") or "unknown")
if state.get("running") is True:
    latest = state.get("latest") if isinstance(state.get("latest"), dict) else {}
    direct = latest.get("direct") if isinstance(latest.get("direct"), dict) else {}
    audio = latest.get("audio") if isinstance(latest.get("audio"), dict) else {}
    if audio.get("ok") is not True:
        audio_label = "audio=probe-fail"
    else:
        audio_label = f"audio={'on' if audio.get('non_silent') is True else 'silent'} max={audio.get('max_db', '?')}dB"
    elapsed = int(float(state.get("elapsed_sec") or 0))
    print(f"running|{audio_label} t={elapsed}s fps={direct.get('fps', '?')} speed={direct.get('speed', '?')}")
    raise SystemExit

summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
audio_ratio = summary.get("combined_audio_present_ratio")
audio_label = "?" if audio_ratio is None else f"{float(audio_ratio) * 100:.1f}%"
print(
    f"{name}|fps={summary.get('mean_output_fps', '?')} "
    f"speed={summary.get('speed_p05', '?')} audio={audio_label} "
    f"relay={summary.get('relay_publisher_connection_count_min', '?')}-"
    f"{summary.get('relay_publisher_connection_count_max', '?')}"
)
PY
}

_direct_av_sync_status_line() {
	python3 - "$DIRECT_AV_SYNC_STATE_FILE" <<'PY' 2>/dev/null || printf 'unknown|status unavailable'
import json
from pathlib import Path
import sys

try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    print("not_started|not tested")
    raise SystemExit
result = state.get("result") if isinstance(state.get("result"), dict) else {}
name = str(state.get("state") or "unknown")
print(
    f"{name}|pairs={result.get('pair_count', '?')} "
    f"max={result.get('max_abs_offset_ms', '?')}ms drift={result.get('drift_ms', '?')}ms"
)
PY
}

# PIDが生きていて指定パターンのプロセスかチェック
_pid_alive_as() {
	local pid="$1" pattern="$2"
	[[ "$pid" -ne 0 ]] 2>/dev/null || return 1
	_pid_exists "$pid" || return 1
	local cmd=$(ps -p "$pid" -o command= 2>/dev/null)
	echo "$cmd" | grep -q "$pattern"
}

# PIDの経過時間を返す
_pid_elapsed() {
	local pid="$1"
	local pid_start=$(ps -p "$pid" -o lstart= 2>/dev/null)
	[[ -n "$pid_start" ]] || return
	local start_epoch=$(date -j -f "%a %b %d %T %Y" "$pid_start" "+%s" 2>/dev/null)
	local now_epoch=$(date "+%s")
	[[ -n "$start_epoch" ]] || return
	local elapsed=$(( now_epoch - start_epoch ))
	if (( elapsed < 60 )); then
		echo "${elapsed}s"
	else
		echo "$(( elapsed / 60 ))m$(( elapsed % 60 ))s"
	fi
}

_find_process_pid() {
	local pattern="$1"
	LC_ALL=C ps -Ao pid=,command= 2>/dev/null | LC_ALL=C awk -v pattern="$pattern" -v self="$$" '
		$1 == self { next }
		$0 ~ pattern && $0 !~ /awk -v pattern/ {
			print $1
			exit
		}
	'
}

_worker_duplicates_from_ps_fallback() {
	local snapshot_file="tmp/state/show_status_worker_ps.$$.txt"
	mkdir -p tmp/state 2>/dev/null || true
	if ! LC_ALL=C ps -Ao pid=,ppid=,command= >"$snapshot_file" 2>/dev/null; then
		rm -f "$snapshot_file" 2>/dev/null || true
		return 1
	fi
	python3 - "$snapshot_file" <<'PY' 2>/dev/null || true
import os
import re
import subprocess
import sys
import time

snapshot_file = sys.argv[1] if len(sys.argv) > 1 else ""
patterns = {
    "soren_loop": r"[/ ]soren_loop[.]sh([ \t]|$)",
    "chat_worker": r"[/ ]workers/chat_worker[.]sh([ \t]|$)",
    "youtube_worker": r"[/ ]workers/youtube_worker[.]sh([ \t]|$)",
    "audio_worker": r"[/ ]workers/audio_worker[.]sh([ \t]|$)",
    "deadline_monitor": r"[/ ]workers/deadline_monitor[.]sh([ \t]|$)|[/ ]deadline_misplacement_monitor[.]py([ \t]|$)",
    "radio_worker": r"[/ ]workers/radio_worker[.]sh([ \t]|$)",
    "prediction_worker": r"[/ ]workers/prediction_worker[.]sh([ \t]|$)",
    "improve_daemon": r"[/ ]improve_daemon[.]sh([ \t]|$)",
}
rows = []
try:
    with open(snapshot_file, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
except Exception:
    raw = ""
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
    for worker_name, pattern in patterns.items():
        rx = re.compile(pattern)
        matched = [(pid, ppid) for pid, ppid, cmd in parsed_rows if rx.search(cmd)]
        matched_pids = {pid for pid, _ppid in matched}
        roots[worker_name] = sorted(
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

duplicates = []
for name in patterns:
    root_pids = sorted(
        set(first_roots.get(name, [])) & set(second_roots.get(name, [])),
        key=lambda p: int(p),
    )
    if len(root_pids) > 1:
        duplicates.append((name, root_pids))
if duplicates:
    detail = "; ".join(f"{name} x{len(pids)} pids={','.join(pids)}" for name, pids in duplicates)
    print(f"duplicate:{detail[:120]}")
elif rows:
    print("ok:")
else:
    print("unknown:duplicate scan unavailable")
PY
	rm -f "$snapshot_file" 2>/dev/null || true
}

_recent_file_active() {
	local f="$1" max_age="$2" recent_mod="" age=""
	[[ -f "$f" ]] || return 1
	case "$max_age" in ''|*[!0-9]*) return 1 ;; esac
	recent_mod=$(stat -f %m "$f" 2>/dev/null) \
		|| recent_mod=$(stat -c %Y "$f" 2>/dev/null) \
		|| recent_mod=""
	case "$recent_mod" in ''|*[!0-9]*) return 1 ;; esac
	age=$(( $(date +%s) - recent_mod ))
	(( age >= 0 && age <= max_age ))
}

_activity_label() {
	local f="$1" label="$2" age=""
	age=$(_file_age "$f")
	[[ -n "$age" ]] || age="recent"
	printf 'activity:%s %s' "$label" "$age"
}

# ファイルの経過時間を返す
_file_age() {
	local f="$1" file_mod="" age=""
	[[ -f "$f" ]] || return
	file_mod=$(stat -f %m "$f" 2>/dev/null) \
		|| file_mod=$(stat -c %Y "$f" 2>/dev/null) \
		|| file_mod=""
	case "$file_mod" in ''|*[!0-9]*) return ;; esac
	age=$(( $(date +%s) - file_mod ))
	if (( age < 60 )); then
		echo "${age}s ago"
	elif (( age < 3600 )); then
		echo "$(( age / 60 ))m ago"
	else
		echo "$(( age / 3600 ))h ago"
	fi
}

_bar_meter() {
	local value="$1" max="$2" width="$3"
	(( max <= 0 )) && max=1
	(( value < 0 )) && value=0
	local filled=$(( value * width / max ))
	(( filled > width )) && filled=$width
	local empty=$(( width - filled ))
	printf "%${filled}s" "" | tr ' ' '█'
	printf "%${empty}s" "" | tr ' ' '·'
}

_truncate_display_width() {
	local text="$1" max_width="$2"
	python3 - "$text" "$max_width" <<'PY' 2>/dev/null
import sys
import unicodedata

text = sys.argv[1]
max_width = int(sys.argv[2])

def ch_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1

width = 0
for ch in text:
    width += ch_width(ch)

if width <= max_width:
    print(text)
    raise SystemExit(0)

ellipsis = ".."
ellipsis_w = 2
limit = max(0, max_width - ellipsis_w)
out = []
cur = 0
for ch in text:
    w = ch_width(ch)
    if cur + w > limit:
        break
    out.append(ch)
    cur += w
print("".join(out) + ellipsis)
PY
}

_truncate_display_width_keep_tail() {
	local text="$1" max_width="$2"
	python3 - "$text" "$max_width" <<'PY' 2>/dev/null
import sys
import unicodedata

text = sys.argv[1]
max_width = int(sys.argv[2])

def ch_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1

width = 0
for ch in text:
    width += ch_width(ch)

if width <= max_width:
    print(text)
    raise SystemExit(0)

ellipsis = ".."
ellipsis_w = 2
limit = max(0, max_width - ellipsis_w)
out = []
cur = 0
for ch in reversed(text):
    w = ch_width(ch)
    if cur + w > limit:
        break
    out.append(ch)
    cur += w
print(ellipsis + "".join(reversed(out)))
PY
}

_print_ai_output_lines() {
	local ai_block="$1" ai_age="$2"
	[[ -n "$ai_block" ]] || return

	local max_ai=$(( W - 24 ))
	local first_line=true
	local ai_line=""
	while IFS= read -r ai_line; do
		[[ -n "${ai_line//[[:space:]]/}" ]] || continue
		ai_line=$(_truncate_display_width "$ai_line" "$max_ai")
		if $first_line; then
			printf "    ${C_WHITE}▸${C_RESET} AIOutput    ${C_DIM}%s${C_RESET}" "$ai_line"
			[[ -n "$ai_age" ]] && printf "  ${C_DIM}(%s)${C_RESET}" "$ai_age"
			echo ""
			first_line=false
		else
			printf "    ${C_WHITE}│${C_RESET}             ${C_DIM}%s${C_RESET}\n" "$ai_line"
		fi
	done <<<"$ai_block"
}

_format_regression_detail_lines() {
	local detail="$1"
	python3 - "$detail" <<'PY' 2>/dev/null
import shlex
import sys

detail = sys.argv[1]
try:
    parts = shlex.split(detail)
except Exception:
    parts = detail.split()

if not parts:
    print("N/A")
    raise SystemExit(0)

state = parts[0]
tokens = parts[1:]
head = [state]
gap = []
best_meta = []
best_gap = []
budget = []
other = []
n_token = ""

for token in tokens:
    if token.startswith("n="):
        n_token = token
    elif token == "hard":
        head.append(token)
    elif token.startswith(("anchor=", "a=")):
        head.append(token)
    elif token.startswith(("gap=", "br=")):
        gap.append(token)
    elif token.startswith(("best=", "bbr=")):
        best_meta.append(token)
    elif token.startswith(("bc", "bm", "bq")):
        best_gap.append(token)
    elif token.startswith(("depth=", "games=", "patience=")):
        budget.append(token)
    else:
        other.append(token)

if n_token:
    head.append(n_token)

lines = [" ".join(head)]
for group in (gap, best_meta, best_gap, budget, other):
    if group:
        lines.append(" ".join(group))

for line in lines:
    print(line)
PY
}

_print_regression_detail() {
	local detail="$1" color="$2"
	local first_line=true
	local max_head=$(( W - 18 ))
	local max_cont=$(( W - 18 ))
	local reg_line=""
	while IFS= read -r reg_line; do
		[[ -n "$reg_line" ]] || continue
		if $first_line; then
			reg_line=$(_truncate_display_width "$reg_line" "$max_head")
			printf "    ${C_WHITE}▸${C_RESET} Regression  ${color}%s${C_RESET}\n" "$reg_line"
			first_line=false
		else
			reg_line=$(_truncate_display_width "$reg_line" "$max_cont")
			printf "    ${C_WHITE}│${C_RESET}             ${color}%s${C_RESET}\n" "$reg_line"
		fi
	done <<<"$(_format_regression_detail_lines "${detail:-N/A}")"
}

_latest_drop_signature() {
	[[ -f "$LATEST_DROP_LOG" ]] || {
		printf 'missing'
		return
	}
	local stat_sig last_turn
	stat_sig=$(stat -f '%m:%z' "$LATEST_DROP_LOG" 2>/dev/null || printf 'unknown')
	last_turn=$(tail -n 1 "$LATEST_DROP_LOG" 2>/dev/null | sed -nE 's/.*"turn"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')
	printf '%s:%s' "$stat_sig" "$last_turn"
}

_latest_drop_summary() {
	[[ -f "$LATEST_DROP_LOG" ]] || return
	python3 - "$LATEST_DROP_LOG" <<'PY' 2>/dev/null
import json
import re
import sys

path = sys.argv[1]

last = ""
try:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
except Exception:
    raise SystemExit(0)

if not last:
    raise SystemExit(0)

try:
    d = json.loads(last)
except Exception:
    raise SystemExit(0)

def number(value, digits=2, signed=False):
    try:
        x = float(value)
    except Exception:
        return "?"
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):+d}" if signed else str(int(round(x)))
    sign = "+" if signed else ""
    return f"{x:{sign}.{digits}f}"

turn_flag = "!" if (d.get("deadline_crossed") or d.get("decision_crosses_deadline")) else ""
turn = f"{d.get('turn', '?')}{turn_flag}"
x = number(d.get("decision_x"), 2, signed=True)
score = number(d.get("score"), 0)
delta = number(d.get("score_delta"), 0, signed=True)
pieces = number(d.get("piece_count"), 0)
next_type = d.get("next_type", "?")
reason = re.sub(r"\s+", "_", str(d.get("decision_reason", "") or "")).strip("_")
labels = []
try:
    with open("strategy.py", encoding="utf-8") as sf:
        src = sf.read()
    labels = re.findall(r"reasons\.append\(\s*['\"]([^'\"]+)['\"]\s*\)", src)
except Exception:
    labels = []
labels = sorted(set(labels), key=len, reverse=True)

matches = [label for label in labels if label and label in reason]
noise_words = ("PENALTY", "CROSSES_DEADLINE_NO_MERGE")
decision = next((label for label in matches if not any(word in label for word in noise_words)), "")
if not decision:
    decision = matches[0] if matches else (reason or "?")

parts = [
    f"T{turn}",
    f"x={x}",
    f"D={decision}",
]

print(" ".join(parts))
PY
}

_fullscreen_commands() {
	local -a cmds
	cmds=()
	(( $+commands[genact] )) && cmds+=("genact")
	(( $+commands[cmatrix] )) && cmds+=("cmatrix")
	(( $+commands[tty-clock] )) && cmds+=("tty-clock")
	(( $+commands[sl] )) && cmds+=("sl")
	printf '%s\n' "${cmds[@]}"
}

_run_fullscreen_command() {
	local cmd="$1"
	case "$cmd" in
	genact)
		timeout 12 genact 2>/dev/null || true
		printf '\033[0m\n'
		;;
	cmatrix)
		timeout 10 cmatrix -b 2>/dev/null || true
		printf '\033[0m\n'
		;;
	tty-clock)
		timeout 8 tty-clock -sC 1 2>/dev/null || true
		printf '\033[0m\n'
		;;
	sl)
		local -a sl_combos
		sl_combos=("" "-a" "-l" "-F" "-c" "-al" "-aF" "-ac" "-lF" "-lc" "-Fc" "-alF" "-alc" "-aFc" "-lFc" "-alFc")
		local opts="${sl_combos[$((RANDOM % ${#sl_combos} + 1))]}"
		sl ${opts} 2>/dev/null </dev/null || true
		printf '\033[0m\n'
		;;
	esac
}

_maybe_run_fullscreen_random() {
	[[ "${FULLSCREEN_ENABLED}" = "1" ]] || return 1
	(( FULLSCREEN_RARE_N > 0 )) || return 1

	local -a cmds
	cmds=("${(@f)$(_fullscreen_commands)}")
	(( ${#cmds} > 0 )) || return 1

	local now
	now=$(date +%s)
	if [[ -f "$FULLSCREEN_LAST_FILE" ]]; then
		local last
		last=$(cat "$FULLSCREEN_LAST_FILE" 2>/dev/null)
		case "$last" in
		''|*[!0-9]*) ;;
		*)
			(( now - last < FULLSCREEN_MIN_GAP_SEC )) && return 1
			;;
		esac
	fi

	(( RANDOM % FULLSCREEN_RARE_N == 0 )) || return 1

	local pick="${cmds[$((RANDOM % ${#cmds} + 1))]}"
	printf '\033[2J\033[H'
	_run_fullscreen_command "$pick"
	sleep 2
	echo "$now" >"$FULLSCREEN_LAST_FILE"
	return 0
}

#=== メイン表示 ===
show_status() {
	# --- 改善プロセス状態 ---
	local imp_status="idle" imp_pid=0 imp_hash="" imp_phase="" imp_progress=0 imp_updated_at=0
	local imp_monitor_status="" imp_monitor_action="" imp_monitor_stale_sec=0
	local ai_backoff_lines=""
	ai_backoff_lines=$(_ai_backoff_status_lines)
	if [[ -f "$TMP_STATE_DIR/improve_state.json" ]]; then
		eval $(python3 -c "
import json, shlex
d=json.load(open('$TMP_STATE_DIR/improve_state.json'))
print('imp_status=' + shlex.quote(str(d.get('status', 'idle'))))
print(f'imp_pid={d.get(\"pid\",0)}')
print('imp_hash=' + shlex.quote(str(d.get('strategy_hash_before', ''))))
print('imp_phase=' + shlex.quote(str(d.get('phase', ''))))
print(f'imp_progress={int(d.get(\"progress\",0) or 0)}')
print(f'imp_updated_at={int(d.get(\"updated_at\",0) or 0)}')
" 2>/dev/null)
	fi
	if [[ -f "$TMP_STATE_DIR/improve_monitor_status.json" ]]; then
		eval $(python3 -c "
import json, shlex
d=json.load(open('$TMP_STATE_DIR/improve_monitor_status.json'))
print('imp_monitor_status=' + shlex.quote(str(d.get('status', ''))))
print('imp_monitor_action=' + shlex.quote(str(d.get('action', ''))))
try:
    stale = int(d.get('stale_sec', 0) or 0)
except Exception:
    stale = 0
print(f'imp_monitor_stale_sec={stale}')
" 2>/dev/null)
	fi

	local imp_alive=false imp_elapsed=""
	if _pid_alive_as "$imp_pid" "eloop_improve"; then
		imp_alive=true
		imp_elapsed=$(_pid_elapsed "$imp_pid")
	fi
	local imp_state_activity_fresh=false
	if [[ "$imp_status" == "running" && "$imp_monitor_status" == "running" && "$imp_monitor_action" == "state_activity_fresh" ]]; then
		imp_state_activity_fresh=true
	fi
	local improve_ai_log="$TMP_DEBUG_DIR/improve_ai.log"
	local improve_hidden_pid_fresh_sec="${SHOW_STATUS_IMPROVE_HIDDEN_PID_FRESH_SEC:-300}"
	case "$improve_hidden_pid_fresh_sec" in ''|*[!0-9]*) improve_hidden_pid_fresh_sec=300 ;; esac
	if [[ "$imp_status" == "running" && "$imp_state_activity_fresh" == false ]]; then
		local now_epoch updated_age=999999
		now_epoch=$(date +%s)
		if [[ "${imp_updated_at:-0}" =~ ^[0-9]+$ ]] && (( imp_updated_at > 0 )); then
			updated_age=$(( now_epoch - imp_updated_at ))
		fi
		if (( updated_age >= 0 && updated_age <= improve_hidden_pid_fresh_sec )) || _file_recent "$improve_ai_log" "$improve_hidden_pid_fresh_sec"; then
			imp_state_activity_fresh=true
			imp_monitor_stale_sec=$(( updated_age >= 0 && updated_age < 999999 ? updated_age : 0 ))
		fi
	fi
	local imp_ai_source="" imp_ai_output_block="" imp_ai_age=""
	if [[ -f "$improve_ai_log" ]] && [[ -s "$improve_ai_log" ]]; then
		local ai_tail_lines="${SHOW_STATUS_AI_TAIL_LINES:-400}"
		local ai_max_lines="${SHOW_STATUS_AI_OUTPUT_LINES:-6}"
		case "$ai_tail_lines" in
		''|*[!0-9]*) ai_tail_lines=400 ;;
		esac
		case "$ai_max_lines" in
		''|*[!0-9]*) ai_max_lines=6 ;;
		esac
		[ "$ai_tail_lines" -lt 50 ] && ai_tail_lines=50
		[ "$ai_max_lines" -lt 1 ] && ai_max_lines=1

		imp_ai_source=$(tail -n "$ai_tail_lines" "$improve_ai_log" 2>/dev/null | grep '\[AI:.*\] START' | tail -1 | sed -E 's/^\[[0-9:]+\] \[AI:[^]]+\] START //')
		imp_ai_output_block=$(tail -n "$ai_tail_lines" "$improve_ai_log" 2>/dev/null | LC_ALL=C awk '
/\[AI:[^]]+\] START/ { capture=1; block=""; next }
capture && /\[AI:[^]]+\] END/ { capture=0; next }
capture { block = block $0 ORS }
END { printf "%s", block }
')
		imp_ai_source=$(printf '%s' "$imp_ai_source" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x1f]//g')
		imp_ai_output_block=$(printf '%s' "$imp_ai_output_block" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/\r//g; s/[\x00-\x08\x0B-\x1F\x7F]//g')
		imp_ai_output_block=$(printf '%s\n' "$imp_ai_output_block" \
			| sed '/^[[:space:]]*$/d' \
			| grep -v 'opencode thinking' \
			| grep -v '^Continue if you have next steps' \
			| grep -v '^[[:space:]]*[✱→←] ' \
			| LC_ALL=C awk 'line != prev { print; prev=line }' \
			| tail -n "$ai_max_lines")
		if [[ -z "$imp_ai_output_block" ]]; then
			# START/END が取れない場合でも、直近の改善ログを最低限見せる
			imp_ai_output_block=$(tail -n "$ai_tail_lines" "$improve_ai_log" 2>/dev/null \
				| perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/\r//g; s/[\x00-\x08\x0B-\x1F\x7F]//g' \
				| grep -v '^\s*$' \
				| grep -v 'opencode thinking' \
				| grep -v '^Continue if you have next steps' \
				| grep -v '^[[:space:]]*[✱→←] ' \
				| grep -v '\[IMPROVE\] job start' \
				| grep -v '\[IMPROVE\] attached pid=' \
				| LC_ALL=C awk 'line != prev { print; prev=line }' \
				| tail -n "$ai_max_lines")
			if [[ -z "$imp_ai_source" ]] && [[ -n "$imp_ai_output_block" ]]; then
				imp_ai_source="fallback:recent improve log"
			fi
		fi
		imp_ai_age=$(_file_age "$improve_ai_log")
	fi

	# --- soren_loop 状態 ---
	local loop_running=false loop_pid=""
	if [[ -f tmp/.soren_loop.lock/pid ]]; then
		loop_pid=$(cat tmp/.soren_loop.lock/pid 2>/dev/null)
		if [[ -n "$loop_pid" ]] && _pid_exists "$loop_pid"; then
			loop_running=true
		fi
	fi
	if ! $loop_running; then
		loop_pid=$(_find_process_pid '[/ ]soren_loop[.]sh([[:space:]]|$)')
		if [[ -n "$loop_pid" ]] && _pid_exists "$loop_pid"; then
			loop_running=true
		fi
	fi
	if ! $loop_running && _recent_file_active "logs/soren_loop.log" 600; then
		loop_running=true
		loop_pid=$(_activity_label "logs/soren_loop.log" "log")
	fi

	# --- ゲーム状態 ---
	local game_state="" game_score=0 game_pieces=0
	if [[ -f game_state.json ]]; then
		eval $(python3 -c "
import json, shlex
d=json.load(open('game_state.json'))
print('game_state=' + shlex.quote(str(d.get('state', '?'))))
print(f'game_score={d.get(\"score\",0)}')
print(f'game_pieces={len(d.get(\"pieces\",[]))}')
" 2>/dev/null)
	fi

	# --- 蓄積ゲーム ---
	local acc_count=0 acc_scores="" acc_russia_count=0 acc_soviet=false acc_max_type=0
	if [[ -f $TMP_STATE_DIR/accumulated_games.json ]]; then
		local current_hash_for_acc=""
		current_hash_for_acc=$(python3 extract_decide_hash.py strategy.py 2>/dev/null || echo "")
		acc_count=$(python3 -c "import json; d=json.load(open('$TMP_STATE_DIR/accumulated_games.json')); h=d.get('hash',''); print(d.get('count',0) if (h and h == '$current_hash_for_acc') else 0)" 2>/dev/null)
		acc_scores=$(python3 -c "import json; d=json.load(open('$TMP_STATE_DIR/accumulated_games.json')); h=d.get('hash',''); print(d.get('scores','') if (h and h == '$current_hash_for_acc') else '')" 2>/dev/null)
		eval $(python3 -c "
import json, shlex
d=json.load(open('$TMP_STATE_DIR/accumulated_games.json'))
h=d.get('hash','')
if h and h == '$current_hash_for_acc':
    print('acc_russia_count=' + shlex.quote(str(int(d.get('russia_count', 0) or 0))))
    print('acc_soviet=' + shlex.quote('true' if d.get('soviet', False) else 'false'))
    print('acc_max_type=' + shlex.quote(str(int(d.get('best_max_type', 0) or 0))))
else:
    print('acc_russia_count=0')
    print('acc_soviet=false')
    print('acc_max_type=0')
" 2>/dev/null)
	fi

	# --- リジェクト履歴 ---
	local rejected_count=0
	[[ -f $TMP_HISTORY_DIR/rejected_hashes.txt ]] && rejected_count=$(python3 - <<PY 2>/dev/null
import json
import time
from pathlib import Path

rejected_path = Path("$TMP_HISTORY_DIR/rejected_hashes.txt")
meta_path = Path("$TMP_STATE_DIR/rejected_hash_metrics.json")
ttl_sec = int(${REJECTED_REEVALUATE_TTL_SEC})

try:
    hashes = [line.strip() for line in rejected_path.read_text().splitlines() if line.strip()]
except Exception:
    print(0)
    raise SystemExit

try:
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if not isinstance(meta, dict):
        meta = {}
except Exception:
    meta = {}

now = int(time.time())
active = 0
for hash_ in hashes:
    entry = meta.get(hash_)
    if not isinstance(entry, dict):
        continue
    updated_at = int(entry.get("updated_at", 0) or 0)
    if updated_at <= 0:
        continue
    if ttl_sec > 0 and now - updated_at >= ttl_sec:
        continue
    active += 1

print(active)
PY
)

	# --- リバートバックアップ ---
	local revert_available=false
	[[ -f tmp/revert_strategy.py ]] && revert_available=true

	# --- 帯域脱出・停滞監視 ---
		local stagnation_count=0 regression_streak=0 stagnation_event="none" stagnation_age="n/a" stagnation_defer_label="" fresh_objective_label="none" wildcard_origin_count=0 wildcard_eval_name="WildEval" wildcard_eval_label="none" annealing_label="none"
	if [[ -f "$TMP_STATE_DIR/stagnation_counter.json" ]]; then
		eval $(python3 - "$TMP_STATE_DIR/stagnation_counter.json" <<'PY' 2>/dev/null
import json
import shlex
import sys
import time

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8")) or {}
except Exception:
    data = {}
count = int(data.get("consecutive_no_improve", 0) or 0)
regression_streak = int(data.get("regression_streak", 0) or 0)
event = str(data.get("last_event", "unknown") or "unknown")
updated = int(data.get("updated_at", 0) or 0)
age = "n/a"
if updated > 0:
    diff = max(0, int(time.time()) - updated)
    if diff < 60:
        age = f"{diff}s"
    elif diff < 3600:
        age = f"{diff // 60}m"
    else:
        age = f"{diff // 3600}h"
print(f"stagnation_count={count}")
print(f"regression_streak={regression_streak}")
print("stagnation_event=" + shlex.quote(event))
print("stagnation_age=" + shlex.quote(age))
PY
)
		fi
		if [[ -f "$TMP_STATE_DIR/accumulated_games.json" || -f "$TMP_STATE_DIR/current_strategy_run.json" ]]; then
			stagnation_defer_label=$(python3 - "$TMP_STATE_DIR/accumulated_games.json" "$TMP_STATE_DIR/current_strategy_run.json" "$current_hash_for_acc" "$MIN_GAMES_BEFORE_REGRESSION" "$WILDCARD_TRIGGER_STAGNATION" "$stagnation_count" "${WILDCARD_EARLY_ESCAPE_MIN_GAMES:-4}" <<'PY' 2>/dev/null
import json
import os
import sys

acc_path, current_path, current_hash, mature_s, trigger_s, count_s, early_min_s = sys.argv[1:8]
try:
    mature_n = max(1, int(mature_s or 12))
except Exception:
    mature_n = 12
try:
    early_min = max(1, int(early_min_s or 4))
except Exception:
    early_min = 4
try:
    trigger = int(trigger_s or 3)
    count = int(count_s or 0)
except Exception:
    trigger = 3
    count = 0
if count < trigger:
    raise SystemExit
data = {}
source = "none"
acc_exists = os.path.exists(acc_path)
if acc_exists:
    try:
        acc = json.load(open(acc_path, encoding="utf-8")) or {}
    except Exception:
        acc = {}
    if current_hash and str(acc.get("hash", "") or "") == current_hash:
        data = acc
        source = "accumulated"
if not data and not acc_exists:
    try:
        data = json.load(open(current_path, encoding="utf-8")) or {}
        source = "current_strategy"
    except Exception:
        data = {}
if not data:
    raise SystemExit
if source == "accumulated":
    raw_scores = data.get("scores", "")
    if isinstance(raw_scores, str):
        score_count = len([x for x in raw_scores.split() if x.strip()])
    elif isinstance(raw_scores, list):
        score_count = len(raw_scores)
    else:
        score_count = 0
    games = int(data.get("count", 0) or score_count)
else:
    games = int(data.get("games_total", 0) or len(data.get("scores") or []))
try:
    russia = int(data.get("russia_count", 0) or 0)
except Exception:
    russia = 0
try:
    soviet = int(data.get("soviet_count", 0) or 0)
except Exception:
    soviet = 0
if not soviet and bool(data.get("soviet", False)):
    soviet = 1
best_type = int(data.get("best_max_type", 0) or 0)
bits = []
if russia > 0:
    bits.append(f"R{russia}")
if soviet > 0:
    bits.append(f"S{soviet}")
if best_type >= 15:
    bits.append(f"T{best_type}")
if bits:
    print(f"defer={','.join(bits)}{games}/{mature_n}")
elif source == "accumulated" and games < early_min:
    print(f"defer=early{games}/{early_min}")
PY
)
		fi
		if [[ -f tmp/improve.lock ]]; then
			fresh_objective_label=$(python3 - tmp/improve.lock <<'PY' 2>/dev/null || echo "none"
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8")) or {}
except Exception:
    data = {}
if not data.get("fresh_objective_same_hash_lock"):
    print("none")
    raise SystemExit
trigger = str(data.get("fresh_objective_trigger") or "objective")
sample_n = int(data.get("fresh_objective_sample_n", 0) or 0)
count = int(data.get("count", sample_n) or sample_n)
fresh_best = int(data.get("fresh_objective_fresh_best_max_type", 0) or 0)
t14_peak = int(data.get("fresh_objective_t14_peak", 0) or 0)
reference = str(data.get("fresh_objective_reference") or "none")
route = str(data.get("improve_reason") or "normal")
print(f"locked {trigger} {sample_n}/{count} T{fresh_best} T14p{t14_peak} ref={reference} route={route}")
PY
)
		fi
		if [[ "$fresh_objective_label" == "none" && -f "$TMP_STATE_DIR/accumulated_games.json" && -f "$TMP_STATE_DIR/current_strategy_run.json" ]]; then
			fresh_objective_label=$(python3 - "$TMP_STATE_DIR/accumulated_games.json" "$TMP_STATE_DIR/current_strategy_run.json" "$TMP_STATE_DIR/best_strategy_anchor.json" "${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_MIN_BEST_TYPE:-14}" "${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MIN_GAMES:-3}" "${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MAX_BEST_TYPE:-13}" <<'PY' 2>/dev/null || echo "none"
import json
import os
import sys

acc_file, current_file, anchor_file, min_best_raw, low_stage_min_raw, low_stage_max_raw = sys.argv[1:7]

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

acc = load(acc_file)
current = load(current_file)
anchor = load(anchor_file)
acc_hash = str(acc.get("hash") or "")
current_hash = str(current.get("hash") or "")
if not acc_hash or acc_hash != current_hash:
    print("none")
    raise SystemExit
acc_count = as_int(acc.get("count", 0))
fresh_n = as_int(current.get("_fresh_score_count", 0))
historical_russia = as_int(current.get("russia_count", 0))
historical_best = as_int(current.get("best_max_type", 0))
anchor_russia = as_int(anchor.get("russia_count", 0))
anchor_best = as_int(anchor.get("best_max_type", 0))
reference = "none"
if historical_russia > 0:
    reference = "historical_russia"
elif historical_best >= 15:
    reference = "historical_best"
elif anchor_russia > 0:
    reference = "anchor_russia"
elif anchor_best >= 15:
    reference = "anchor_best"
if reference == "none" or acc_count <= 0:
    print("none")
    raise SystemExit
min_best = max(1, as_int(min_best_raw, 14))
low_stage_min = max(1, as_int(low_stage_min_raw, 3))
low_stage_max = max(1, as_int(low_stage_max_raw, 13))
max_types = [as_int(x) for x in (current.get("max_types") or [])]
fresh_types = max_types[-fresh_n:] if fresh_n > 0 else []
fresh_best = max(fresh_types or [0])
fresh_russia = sum(1 for value in fresh_types if value >= 15)
if fresh_russia > 0:
    print("none")
elif 0 < fresh_best <= low_stage_max:
    state = "ready low_stage_miss" if acc_count >= low_stage_min else "wait low_stage_miss"
    print(f"{state} {acc_count}/{low_stage_min} T{fresh_best} ref={reference}")
elif fresh_best >= min_best:
    state = "ready high_frontier_miss" if acc_count >= low_stage_min else "wait high_frontier_miss"
    print(f"{state} {acc_count}/{low_stage_min} T{fresh_best} ref={reference}")
else:
    print("none")
PY
)
		fi
		if [[ -f "$TMP_STATE_DIR/wildcard_origin.json" ]]; then
		wildcard_origin_count=$(python3 - "$TMP_STATE_DIR/wildcard_origin.json" <<'PY' 2>/dev/null
import json
import sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8")) or {}
except Exception:
    data = {}
if not isinstance(data, dict):
    print(0)
    raise SystemExit
print(sum(
    1
    for meta in data.values()
    if str((meta or {}).get("origin_type") or "wildcard") == "wildcard"
))
PY
)
	fi
	case "$wildcard_origin_count" in
	''|*[!0-9]*) wildcard_origin_count=0 ;;
	esac
		if [[ -f "$TMP_STATE_DIR/wildcard_origin.json" ]]; then
			eval $(python3 - "$TMP_STATE_DIR/wildcard_origin.json" "$TMP_STATE_DIR/current_strategy_run.json" "$TMP_STATE_DIR/wildcard_outcomes.jsonl" "$MIN_GAMES_BEFORE_REGRESSION" "$TMP_STATE_DIR/best_strategy_anchor.json" <<'PY' 2>/dev/null
import json
import os
import shlex
import sys

origin_file, current_file, outcome_file, mature_raw, anchor_file = sys.argv[1:6]
try:
    mature_n = max(1, int(mature_raw))
except Exception:
    mature_n = 12

try:
    origins = json.load(open(origin_file, encoding="utf-8")) or {}
except Exception:
    origins = {}
try:
    current = json.load(open(current_file, encoding="utf-8")) or {}
except Exception:
    current = {}

h = str(current.get("hash", "") or "")
label = "none"
if h and isinstance(origins, dict) and h in origins:
    origin = origins.get(h) if isinstance(origins.get(h), dict) else {}
    origin_type = str(origin.get("origin_type", "") or "wildcard")
    eval_name = {
        "archive_restart": "ArcEval",
        "escape_ai": "AIEval",
        "wildcard": "WildEval",
    }.get(origin_type, "VarEval")
    scores = []
    for raw in current.get("scores", []) or []:
        try:
            scores.append(float(raw))
        except Exception:
            pass
    n = len(scores)
    def composite(vals):
        if not vals:
            return 0
        xs = sorted(vals)
        def quantile(vals, p):
            if len(vals) == 1:
                return vals[0]
            pos = (len(vals) - 1) * p
            lo = int(pos)
            hi = min(lo + 1, len(vals) - 1)
            frac = pos - lo
            return vals[lo] * (1.0 - frac) + vals[hi] * frac
        mean = sum(vals) / len(vals)
        if len(vals) > 1:
            var = sum((x - mean) ** 2 for x in vals) / len(vals)
            lcb = mean - 1.28 * ((var ** 0.5) / (len(vals) ** 0.5))
        else:
            lcb = mean
        return int(0.55 * quantile(xs, 0.50) + 0.30 * quantile(xs, 0.25) + 0.15 * lcb)

    comp = composite(scores)
    trend_label = ""
    if len(scores) >= 2:
        trend = comp - composite(scores[:-1])
        trend_label = f" t{trend:+d}"
    event = "pending"
    if outcome_file and os.path.exists(outcome_file):
        try:
            with open(outcome_file, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    row = json.loads(raw)
                    if str(row.get("hash", "") or "") == h:
                        event = str(row.get("event", "") or event)
        except Exception:
            pass
    delta_label = ""
    try:
        anchor = json.load(open(anchor_file, encoding="utf-8")) or {}
        anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
        if anchor_comp:
            delta = int(comp - anchor_comp)
            delta_label = f" d{delta:+d}"
    except Exception:
        pass
    source_label = ""
    if origin_type == "archive_restart":
        source_bits = []
        try:
            source_n = int(origin.get("source_n", 0) or 0)
            if source_n:
                source_bits.append(f"N{source_n}")
        except Exception:
            pass
        try:
            source_russia = int(origin.get("source_russia_count", 0) or 0)
            if source_russia:
                source_bits.append(f"R{source_russia}")
        except Exception:
            pass
        try:
            source_best_type = int(origin.get("source_best_max_type", 0) or 0)
            if source_best_type:
                source_bits.append(f"T{source_best_type}")
        except Exception:
            pass
        if source_bits:
            source_label = " " + "".join(source_bits)
    event_short = {"OK_IDLE": "OKI", "OK_BEAT": "OKB", "REGRESSION": "REG", "PROMOTE": "PRO", "RESET": "RST"}.get(event, event[:3])
    if origin_type == "archive_restart":
        label = f"{h[:4]} {n}/{mature_n} {event_short} c{comp}{delta_label}{source_label}{trend_label}"
    else:
        label = f"{h[:4]} {n}/{mature_n} {event_short} c{comp}{trend_label}{delta_label}"
    print("wildcard_eval_name=" + shlex.quote(eval_name))

print("wildcard_eval_label=" + shlex.quote(label))
PY
)
		fi
		if [[ -f "$TMP_STATE_DIR/annealing_candidates.jsonl" ]]; then
			eval $(python3 - "$TMP_STATE_DIR/annealing_candidates.jsonl" <<'PY' 2>/dev/null
import json
import shlex
import sys
import time

path = sys.argv[1]
last = None
try:
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                last = json.loads(raw)
            except Exception:
                continue
except Exception:
    last = None
if not isinstance(last, dict):
    print("annealing_label=none")
    raise SystemExit
age = max(0, int(time.time()) - int(last.get("epoch", 0) or 0))
if age < 60:
    age_label = f"{age}s"
elif age < 3600:
    age_label = f"{age // 60}m"
else:
    age_label = f"{age // 3600}h"
try:
    prob = float(last.get("accept_probability", 0.0) or 0.0)
except Exception:
    prob = 0.0
try:
    gap = int(float(last.get("comp_gap", 0.0) or 0.0))
except Exception:
    gap = 0
label = f"{str(last.get('hash', '') or '')[:4]} p={prob:.2f} gap={gap} {age_label}"
print("annealing_label=" + shlex.quote(label))
PY
)
		fi

		# --- archive_restart 次候補 / 枯渇観測 ---
		local archive_next_label="none"
		eval $(python3 - \
			"${TMP_STATE_DIR}/rolling_scores.json" \
			"${TMP_STATE_DIR}/best_strategy_anchor.json" \
			"${TMP_STATE_DIR}/rejected_hash_metrics.json" \
			"${TMP_STATE_DIR}/wildcard_origin.json" \
			"${TMP_STATE_DIR}/archive_restart_cooldown.json" \
			"${ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE:-${TMP_STATE_DIR}/.archive_restart_no_candidate}" \
			"${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}" \
			"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}" <<'PY' 2>/dev/null
import json
import math
import os
import shlex
import sys
import time

rolling_file, anchor_file, rejected_file, origin_file, cooldown_file, no_candidate_file, archive_dir, permanent_archive_dir = sys.argv[1:9]

def load(path, default):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if data is not None else default
    except Exception:
        pass
    return default

def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"

def quantile(vals, p):
    xs = sorted(float(v) for v in vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    vals = []
    for raw in scores or []:
        try:
            vals.append(float(raw))
        except Exception:
            pass
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if len(vals) > 1:
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        lcb = mean - 1.28 * (math.sqrt(var) / math.sqrt(len(vals)))
    else:
        lcb = mean
    return {
        "n": len(vals),
        "comp": 0.55 * quantile(vals, 0.50) + 0.30 * quantile(vals, 0.25) + 0.15 * lcb,
        "p25": quantile(vals, 0.25),
    }

def archive_is_runtime_stable(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "BEGIN DEADLINE GUARD" in f.read(200000)
    except Exception:
        return False

def boolish(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}

include_permanent = boolish(os.getenv("ARCHIVE_RESTART_INCLUDE_PERMANENT", "1"), True)
allow_origin_retry = boolish(os.getenv("ARCHIVE_RESTART_ALLOW_ORIGIN_RETRY", "1"), True)

def find_archive_path(h):
    paths = [os.path.join(archive_dir, f"{h}.py")]
    if include_permanent and permanent_archive_dir:
        paths.append(os.path.join(permanent_archive_dir, f"{h}.py"))
    for path in paths:
        if os.path.exists(path) and archive_is_runtime_stable(path):
            return path
    return ""

def archive_path_blocker(h):
    paths = [os.path.join(archive_dir, f"{h}.py")]
    if include_permanent and permanent_archive_dir:
        paths.append(os.path.join(permanent_archive_dir, f"{h}.py"))
    saw_file = False
    for path in paths:
        if not os.path.exists(path):
            continue
        saw_file = True
        if archive_is_runtime_stable(path):
            return ""
    return "unstable" if saw_file else "miss"

def is_cooled_down(h):
    if h not in cooldown_map:
        return False
    try:
        ttl = int(os.getenv("ARCHIVE_RESTART_COOLDOWN_SEC", "21600") or 21600)
    except Exception:
        ttl = 21600
    if ttl <= 0:
        return True
    meta = cooldown_map.get(h) if isinstance(cooldown_map.get(h), dict) else {}
    try:
        epoch = int(meta.get("epoch", 0) or 0)
    except Exception:
        epoch = 0
    return epoch <= 0 or (int(time.time()) - epoch) < ttl

if os.getenv("ARCHIVE_RESTART_ENABLED", "1") != "1":
    print("archive_next_label=" + shlex.quote("none"))
    raise SystemExit

try:
    no_ttl = int(os.getenv("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_SEC", "900") or 900)
except Exception:
    no_ttl = 900
no_candidate_age = None
if os.path.exists(no_candidate_file):
    try:
        age = max(0, int(time.time()) - int(os.path.getmtime(no_candidate_file)))
    except Exception:
        age = no_ttl + 1
    if age < no_ttl:
        no_candidate_age = age

rolling = load(rolling_file, {})
anchor = load(anchor_file, {})
rejected = set(load(rejected_file, {}).keys())
origin_map = load(origin_file, {})
origin = set(origin_map.keys())
cooldown_map = load(cooldown_file, {})
cooldown = set(cooldown_map.keys())
anchor_hash = str(anchor.get("hash", "") or "")
try:
    anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
except Exception:
    anchor_comp = 0.0
try:
    anchor_russia = int(anchor.get("russia_count", 0) or 0)
    anchor_soviet = int(anchor.get("soviet_count", 0) or 0)
except Exception:
    anchor_russia = anchor_soviet = 0
try:
    min_ratio = float(os.getenv("ARCHIVE_RESTART_MIN_COMP_RATIO", "0.92") or 0.92)
except Exception:
    min_ratio = 0.92
try:
    min_best_type = int(os.getenv("ARCHIVE_RESTART_MIN_BEST_TYPE", "14") or 14)
except Exception:
    min_best_type = 14
threshold = anchor_comp * max(0.0, min(1.0, min_ratio)) if anchor_comp > 0 else 0.0
rows = []
for h, entry in (rolling or {}).items():
    h = str(h)
    if not h or h == anchor_hash or h in rejected or is_cooled_down(h):
        continue
    path = find_archive_path(h)
    if not path:
        continue
    m = metrics((entry or {}).get("scores", []) or [])
    if not m or m["n"] < 12 or m["comp"] < threshold:
        continue
    try:
        russia = int((entry or {}).get("russia_count", 0) or 0)
        soviet = int((entry or {}).get("soviet_count", 0) or 0)
        best_type = int((entry or {}).get("best_max_type", 0) or 0)
    except Exception:
        russia = soviet = best_type = 0
    if best_type >= 15 and russia <= 0:
        russia = 1
    if best_type >= 16 and soviet <= 0:
        soviet = 1
    if anchor_soviet > 0 and soviet <= 0:
        continue
    if anchor_russia > 0 and russia <= 0:
        continue
    if russia <= 0 and soviet <= 0 and best_type < min_best_type:
        continue
    origin_type = str((origin_map.get(h) or {}).get("origin_type") or "") if isinstance(origin_map.get(h), dict) else ("legacy_origin" if h in origin else "")
    if origin_type and not (allow_origin_retry and (russia > 0 or soviet > 0 or best_type >= min_best_type)):
        continue
    score = soviet * 100000 + russia * 12000 + max(0, best_type - 13) * 2500 + m["p25"] * 0.08 + m["comp"]
    rows.append((score, h, m, russia, soviet, best_type, origin_type))

rows.sort(reverse=True)
if not rows:
    if no_candidate_age is not None:
        label = f"no cand cd {fmt_age(no_candidate_age)}/{fmt_age(no_ttl)} -> escape_ai"
        print("archive_next_label=" + shlex.quote(label))
        raise SystemExit
    blockers = {}
    def bump(name):
        blockers[name] = blockers.get(name, 0) + 1
    for h, entry in (rolling or {}).items():
        h = str(h)
        if not h or h == anchor_hash:
            continue
        m = metrics((entry or {}).get("scores", []) or [])
        if not m or m["n"] < 12 or m["comp"] < threshold:
            continue
        try:
            russia = int((entry or {}).get("russia_count", 0) or 0)
            soviet = int((entry or {}).get("soviet_count", 0) or 0)
            best_type = int((entry or {}).get("best_max_type", 0) or 0)
        except Exception:
            russia = soviet = best_type = 0
        if best_type >= 15 and russia <= 0:
            russia = 1
        if best_type >= 16 and soviet <= 0:
            soviet = 1
        if russia <= 0 and soviet <= 0 and best_type < min_best_type:
            continue
        if h in rejected:
            bump("reject")
            continue
        if is_cooled_down(h):
            bump("cool")
            continue
        path_blocker = archive_path_blocker(h)
        if path_blocker:
            bump(path_blocker)
            continue
        if anchor_soviet > 0 and soviet <= 0:
            bump("S0")
            continue
        if anchor_russia > 0 and russia <= 0:
            bump("R0")
            continue
        origin_type = str((origin_map.get(h) or {}).get("origin_type") or "") if isinstance(origin_map.get(h), dict) else ("legacy_origin" if h in origin else "")
        if origin_type and not (allow_origin_retry and (russia > 0 or soviet > 0 or best_type >= min_best_type)):
            bump("origin")
    parts = [f"{k}={blockers[k]}" for k in ("R0", "unstable", "miss", "cool", "reject", "S0", "origin") if blockers.get(k)]
    suffix = f" {' '.join(parts)}" if parts else ""
    label = f"no cand c>={int(round(threshold))}{suffix} -> AI"
else:
    _, h, m, russia, soviet, best_type, origin_type = rows[0]
    retry = " retry" if origin_type else ""
    label = f"{h[:4]} c{int(round(m['comp']))} p25{int(round(m['p25']))} n{m['n']} R{russia} S{soviet} T{best_type} pool{len(rows)}{retry}"
print("archive_next_label=" + shlex.quote(label))
PY
)
		archive_next_label=$(_truncate_display_width_keep_tail "$archive_next_label" 48)

		# --- WILDCARD 並列評価の発動失敗監視 ---
		local wildcard_parallel_label=""
		local wildcard_parallel_name="WildParFail"
		if [[ -f "${WILDCARD_PARALLEL_STATUS_FILE:-${TMP_STATE_DIR}/wildcard_parallel_status.json}" ]]; then
			eval $(python3 - "${WILDCARD_PARALLEL_STATUS_FILE:-${TMP_STATE_DIR}/wildcard_parallel_status.json}" <<'PY' 2>/dev/null
import json
import os
import shlex
import sys
import time

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}
phase = str(data.get("phase", "") or "")
detail = str(data.get("detail", "") or "")
params = data.get("params") or {}
is_post_improve = bool(params.get("baseline_slot1")) or "post_improve_param_parallel" in detail
try:
    age = max(0, int(time.time()) - int(os.path.getmtime(path)))
except Exception:
    age = 999999

def fmt_age(seconds):
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"

label = ""
if phase in ("no_candidate", "infra_failed", "failed") and age <= 3600:
    candidates = data.get("candidates", []) or []
    failed = [c for c in candidates if str(c.get("status", "") or "") in ("failed", "timeout")]
    zero_game = [c for c in candidates if int(c.get("games", 0) or 0) <= 0]
    errors = [str(c.get("error", "") or "") for c in failed if str(c.get("error", "") or "")]
    eval_candidates = [c for c in candidates if not bool(c.get("score_baseline"))]
    eval_failed = [c for c in eval_candidates if str(c.get("status", "") or "") in ("failed", "timeout")]
    eval_zero_game = [c for c in eval_candidates if int(c.get("games", 0) or 0) <= 0]
    error_blob = " ".join(errors)
    infra_markers = ("bridge exited", "BRIDGE-EXIT", "SIGABRT", "process did exit", "EADDRINUSE")
    display_phase = phase
    if eval_candidates and len(eval_failed) == len(eval_candidates) and len(eval_zero_game) == len(eval_candidates) and any(m in error_blob for m in infra_markers):
        display_phase = "infra_failed"
    err = errors[0][:28] if errors else "no successful candidates"
    label = f"{display_phase} f{len(failed)}/{len(candidates)} z{len(zero_game)}/{len(candidates)} {fmt_age(age)} {err}"
name = "PostParamFail" if is_post_improve else "WildParFail"
print("wildcard_parallel_name=" + shlex.quote(name))
print("wildcard_parallel_label=" + shlex.quote(label))
PY
)
			wildcard_parallel_label=$(_truncate_display_width "$wildcard_parallel_label" 48)
		fi

		# --- 視聴者チャット観測 ---
		local viewer_chat_label="none"
		if [[ -x ./viewer_chat_monitor.sh ]]; then
			viewer_chat_label=$(./viewer_chat_monitor.sh line 2>/dev/null || echo "none")
			viewer_chat_label=$(_truncate_display_width_keep_tail "$viewer_chat_label" 48)
		fi

		# --- 改善 backoff 観測 ---
		local improve_backoff_label="none"
		local rate_limit_backoff_file="${TMP_STATE_DIR:-tmp/state}/rate_limit_backoff"
		if [[ -f "$rate_limit_backoff_file" ]]; then
			eval $(python3 - "$rate_limit_backoff_file" <<'PY' 2>/dev/null
import shlex
import sys
import time

path = sys.argv[1]
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except Exception:
    lines = []
try:
    count = int(lines[0]) if len(lines) > 0 else 1
except Exception:
    count = 1
try:
    ts = int(lines[1]) if len(lines) > 1 else 0
except Exception:
    ts = 0
exp = min(max(count - 1, 0), 5)
wait = 300 * (1 << exp)
remaining = max(0, wait - max(0, int(time.time()) - ts))
if remaining >= 3600:
    rem = f"{remaining // 3600}h"
elif remaining >= 60:
    rem = f"{remaining // 60}m"
else:
    rem = f"{remaining}s"
label = f"count={count} rem={rem} wait={wait // 60}m"
print("improve_backoff_label=" + shlex.quote(label))
PY
)
		fi

		# --- soren91 改善 watchdog / quarantine 観測 ---
		local soren91_improve_watchdog_label="none"
		eval $(python3 - \
			"${SOREN91_IMPROVE_LOCK:-soren91/tmp/soren91_improve.lock}" \
			"${SOREN91_IMPROVE_PID_FILE:-soren91/tmp/soren91_improve.pid}" \
			"${SOREN91_IMPROVE_HUNG_QUARANTINE_FILE:-tmp/state/soren91_improve_hung_quarantine.jsonl}" <<'PY' 2>/dev/null
import json
import os
import shlex
import sys
import time

lock_file, pid_file, quarantine_file = sys.argv[1:4]
now = int(time.time())

def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"

parts = []
if os.path.exists(lock_file):
    try:
        age = now - int(os.path.getmtime(lock_file))
    except Exception:
        age = 0
    pid = "?"
    try:
        if os.path.exists(pid_file):
            pid = open(pid_file, encoding="utf-8", errors="ignore").read().strip() or "?"
    except Exception:
        pid = "?"
    parts.append(f"lock {fmt_age(age)} pid={pid}")

last = None
if os.path.exists(quarantine_file):
    try:
        with open(quarantine_file, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if isinstance(row, dict):
                    last = row
    except Exception:
        last = None
if isinstance(last, dict):
    try:
        age = now - int(last.get("epoch", 0) or 0)
    except Exception:
        age = 0
    reason = str(last.get("reason") or last.get("event") or "unknown")
    pid = last.get("pid")
    pid_text = "?" if pid in (None, "") else str(pid)
    parts.append(f"last {reason} {fmt_age(age)} pid={pid_text}")

label = "; ".join(parts) if parts else "none"
print("soren91_improve_watchdog_label=" + shlex.quote(label))
PY
)
		soren91_improve_watchdog_label=$(_truncate_display_width_keep_tail "$soren91_improve_watchdog_label" 48)

		# --- 最低試合ゲート ---
		local min_games="${MIN_GAMES_BEFORE_IMPROVE_ENV:-$(_config_int_default MIN_GAMES_BEFORE_IMPROVE 12)}"
	case "$min_games" in
	''|*[!0-9]*) min_games=12 ;;
	esac
	(( min_games < 1 )) && min_games=12

	# --- ロールバック履歴 ---
	local rollback_total=0 rollback_last_at="" rollback_last_age=""
	local -a rollback_events
	while IFS='|' read -r rec_type rec_a rec_b rec_c rec_d; do
		case "$rec_type" in
			COUNT) rollback_total=${rec_a:-0} ;;
			LAST_AT) rollback_last_at="$rec_a" ;;
			LAST_AGE) rollback_last_age="$rec_a" ;;
			EVENT) rollback_events+=("${rec_a}|${rec_b}|${rec_c}|${rec_d}") ;;
		esac
	done < <(python3 - "$SHOW_STATUS_ROLLBACK_HISTORY_LIMIT" <<'PY'
import ast
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys

try:
    event_limit = max(1, int(sys.argv[1]))
except Exception:
    event_limit = 5


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def decide_hash(source):
    if not source:
        return ""
    def stable_ast_dump(node):
        if isinstance(node, ast.AST):
            fields = []
            for field in getattr(node, "_fields", ()):
                value = getattr(node, field)
                if value == [] or value is None:
                    continue
                fields.append(f"{field}={stable_ast_dump(value)}")
            if fields:
                return f"{node.__class__.__name__}({', '.join(fields)})"
            return f"{node.__class__.__name__}()"
        if isinstance(node, list):
            return "[" + ", ".join(stable_ast_dump(item) for item in node) + "]"
        return repr(node)
    try:
        tree = ast.parse(source)
    except Exception:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            normalized = stable_ast_dump(ast.Module(body=body, type_ignores=[]))
            return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]
    return ""


def age_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def load_live_rollback_event():
    path = "tmp/state/last_rollback_pair.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    try:
        epoch = int(data.get("updated_at", 0) or 0)
    except Exception:
        epoch = 0
    if epoch <= 0:
        return None
    from_hash = str(data.get("from_hash") or "?")[:12]
    to_hash = str(data.get("to_hash") or "?")[:12]
    if from_hash == "?" and to_hash == "?":
        return None
    return {
        "dt": dt.datetime.fromtimestamp(epoch).astimezone(),
        "from_hash": from_hash,
        "to_hash": to_hash,
        "target_hash": to_hash if to_hash != "?" else "",
    }


log_text = run(
    [
        "git",
        "log",
        "--date=iso-strict",
        "--pretty=format:%H|%ad|%s",
        "--grep=^eloop Auto-revert: regression detected",
        "-n",
        "200",
    ]
)
if not log_text:
    live_event = load_live_rollback_event()
    if live_event:
        now = dt.datetime.now().astimezone()
        print("COUNT|1")
        print(f"LAST_AT|{live_event['dt'].strftime('%Y-%m-%d %H:%M')}")
        print(f"LAST_AGE|{age_text((now - live_event['dt']).total_seconds())}")
        print(
            "EVENT|"
            f"{live_event['dt'].strftime('%m-%d %H:%M')}|"
            f"{live_event['from_hash']}|{live_event['to_hash']}|{live_event['target_hash']}"
        )
    else:
        print("COUNT|0")
    raise SystemExit(0)

rows = []
for line in log_text.splitlines():
    parts = line.split("|", 2)
    if len(parts) == 3:
        rows.append(parts)

now = dt.datetime.now().astimezone()
live_event = load_live_rollback_event()
live_is_newer = False
try:
    if live_event:
        first_git_dt = dt.datetime.fromisoformat(rows[0][1])
        live_is_newer = live_event["dt"] > first_git_dt
except Exception:
    live_is_newer = bool(live_event)

print(f"COUNT|{len(rows) + (1 if live_is_newer else 0)}")
try:
    last_dt = live_event["dt"] if live_is_newer else dt.datetime.fromisoformat(rows[0][1])
    print(f"LAST_AT|{last_dt.strftime('%Y-%m-%d %H:%M')}")
    print(f"LAST_AGE|{age_text((now - last_dt).total_seconds())}")
except Exception:
    pass

printed = 0
if live_is_newer and live_event:
    print(
        "EVENT|"
        f"{live_event['dt'].strftime('%m-%d %H:%M')}|"
        f"{live_event['from_hash']}|{live_event['to_hash']}|{live_event['target_hash']}"
    )
    printed += 1

for commit, ad, subj in rows[:event_limit]:
    if printed >= event_limit:
        break
    when_disp = ad
    try:
        when_disp = dt.datetime.fromisoformat(ad).strftime("%m-%d %H:%M")
    except Exception:
        pass

    parent = run(["git", "rev-parse", f"{commit}^"])
    src_before = run(["git", "show", f"{parent}:strategy.py"]) if parent else ""
    src_after = run(["git", "show", f"{commit}:strategy.py"])

    from_hash = decide_hash(src_before) or "?"
    to_hash = decide_hash(src_after) or "?"

    target_hash = ""
    m = re.search(r"target=best_comp hash=([0-9a-fA-F]{8,12})", subj)
    if m:
        target_hash = m.group(1).lower()
    if to_hash == "?" and target_hash:
        to_hash = target_hash

    print(f"EVENT|{when_disp}|{from_hash[:12]}|{to_hash[:12]}|{target_hash[:12]}")
    printed += 1
PY
)

	# --- say (TTS) 状態 ---
	local say_running=false say_pid=""
	if [[ -f tmp/.say_queue/pid ]]; then
		say_pid=$(cat tmp/.say_queue/pid 2>/dev/null)
		if [[ -n "$say_pid" ]] && _pid_exists "$say_pid"; then
			say_running=true
		fi
	fi
	# pgrep でも確認 (pidファイルがなくても say が動いている場合)
	if ! $say_running; then
		say_pid=$(pgrep -x say 2>/dev/null | head -1)
		[[ -n "$say_pid" ]] && say_running=true
	fi

	# say_queue のロック状態
	local say_lock_present=false
	local say_lock_stale=false
	local say_lock_owner_alive=false
	local say_lock_age="" say_lock_owner_pid=""
	if [[ -d tmp/.say_queue/.lock ]]; then
		say_lock_present=true
		local say_lock_owner_raw="" say_lock_hb="" say_lock_age_sec=0
		local say_lock_stale_sec=180
		say_lock_owner_raw=$(cat tmp/.say_queue/.lock/owner_pid 2>/dev/null || true)
		say_lock_owner_pid="${say_lock_owner_raw%%:*}"
		case "$say_lock_owner_pid" in
		''|*[!0-9]*) say_lock_owner_pid="" ;;
		esac
		if [[ -n "$say_lock_owner_pid" ]] && _pid_exists "$say_lock_owner_pid"; then
			say_lock_owner_alive=true
		fi
		say_lock_hb=$(cat tmp/.say_queue/.lock/heartbeat 2>/dev/null || true)
		case "$say_lock_hb" in
		''|*[!0-9]*)
			say_lock_hb=$(stat -f %m tmp/.say_queue/.lock 2>/dev/null) \
				|| say_lock_hb=$(stat -c %Y tmp/.say_queue/.lock 2>/dev/null) \
				|| say_lock_hb=0
			;;
		esac
		case "$say_lock_hb" in
		''|*[!0-9]*) say_lock_hb=0 ;;
		esac
		if (( say_lock_hb > 0 )); then
			say_lock_age_sec=$(( $(date +%s) - say_lock_hb ))
			if (( say_lock_age_sec < 60 )); then say_lock_age="${say_lock_age_sec}s"
			else say_lock_age="$(( say_lock_age_sec / 60 ))m$(( say_lock_age_sec % 60 ))s"
			fi
		fi
		if ! $say_lock_owner_alive && (( say_lock_age_sec > say_lock_stale_sec )); then
			say_lock_stale=true
		fi
	fi

	# say_queue の現在ソース (owner|phase|source|ts|label)
	local say_phase="" say_source="" say_source_age="" say_label=""
	local say_source_is_radio=false say_source_is_comment=false
	if [[ -f tmp/.say_queue/current_source ]]; then
		local cs_line="" cs_owner="" cs_ts=""
		cs_line=$(cat tmp/.say_queue/current_source 2>/dev/null || true)
		IFS='|' read -r cs_owner say_phase say_source cs_ts say_label _ <<<"$cs_line"
		case "$cs_ts" in
		''|*[!0-9]*) ;;
		*)
			local cs_age=$(( $(date +%s) - cs_ts ))
			if (( cs_age < 60 )); then say_source_age="${cs_age}s"
			else say_source_age="$(( cs_age / 60 ))m$(( cs_age % 60 ))s"
			fi
			;;
		esac
		case "$say_source" in
		*"/tmp/eloop_radio_talk_"*|*"tmp/.radio_deferred_queue/radio_"*|*"radio_soviet_celebration.txt"*)
			say_source_is_radio=true
			;;
		*"tmp/.comment_queue/comment_"*)
			say_source_is_comment=true
			;;
		esac
		case "$say_label" in
		radio:*)
			say_source_is_radio=true
			;;
		comment*)
			say_source_is_comment=true
			;;
		esac
	fi
	local say_effective_status="silent"
	if $say_running; then
		say_effective_status="playing"
	elif $say_lock_present && (( say_lock_age_sec <= 20 )) && [[ "$say_phase" == "playing" ]]; then
		# macOS/Codex sandbox may deny kill -0 for the actual player PID while
		# the say owner is still refreshing the lock heartbeat. Prefer the live
		# owner heartbeat over a false SILENT display.
		say_effective_status="playing"
	elif $say_lock_present && $say_lock_owner_alive; then
		case "${say_phase:-}" in
		retry_wait) say_effective_status="retry" ;;
		waiting) say_effective_status="waiting" ;;
		playing) say_effective_status="preparing" ;;
		*) say_effective_status="waiting" ;;
		esac
	fi

	# --- VOICEVOX 合成ロック & ストリーミング状態 ---
	local voicevox_synth_locked=false
	[[ -d tmp/.say_queue/.voicevox_synth_lock ]] && voicevox_synth_locked=true

	local stream_active=false stream_chunk_done=0 stream_chunk_total=0
	local stream_dir=""
	stream_dir=$(find tmp/.say_queue -maxdepth 1 -type d -name 'stream_*' 2>/dev/null | head -1)
	if [[ -n "$stream_dir" ]] && [[ -d "$stream_dir" ]]; then
		stream_active=true
		stream_chunk_done=$(find "$stream_dir" -name 'chunk_*.wav' 2>/dev/null | wc -l | tr -d ' ')
		# チャンク総数はチャンクファイル（_chunks.txt）から取得
		local chunks_file=$(find tmp/.say_queue -maxdepth 1 -name 'content_*_chunks.txt' 2>/dev/null | head -1)
		if [[ -n "$chunks_file" ]] && [[ -f "$chunks_file" ]]; then
			stream_chunk_total=$(wc -l < "$chunks_file" | tr -d ' ')
			# +1 for chunk 0 (pre-synthesized)
			stream_chunk_total=$(( stream_chunk_total + 1 ))
		fi
	fi

	# --- ラジオコーナー状態 (状態ファイルベース) ---
	local radio_status="idle" radio_corner="" radio_elapsed=""
	if [[ -f $TMP_STATE_DIR/.radio_state ]]; then
		local radio_line radio_mode radio_ts radio_owner_pid
		radio_line=$(cat $TMP_STATE_DIR/.radio_state 2>/dev/null)
		IFS=':' read -r radio_mode radio_corner radio_ts radio_owner_pid _ <<<"$radio_line"
		if [[ -n "$radio_ts" ]]; then
			local age=$(( $(date +%s) - radio_ts ))
			if (( age > RADIO_STATE_STALE_SEC )); then
				# stale は表示上は無視
				radio_status="idle"
				radio_elapsed=""
				radio_corner=""
			else
				radio_status="$radio_mode"
				if (( age < 60 )); then radio_elapsed="${age}s"
				else radio_elapsed="$(( age / 60 ))m$(( age % 60 ))s"
				fi
			fi
		fi
	fi
	if [[ -z "$radio_corner" ]] && [[ "$say_label" == radio:* ]]; then
		radio_corner="${say_label#radio:}"
	fi
	# 注: sayフォールバックは廃止 (コメント再生との区別不可のため状態ファイルのみで判定)
	# コーナー名が取れなかった場合、過去トピックスから取得
		if [[ -z "$radio_corner" ]] && [[ -f $TMP_HISTORY_DIR/past_radio_topics.txt ]] && [[ -s $TMP_HISTORY_DIR/past_radio_topics.txt ]]; then
			local last_radio_line=$(tail -1 $TMP_HISTORY_DIR/past_radio_topics.txt)
			radio_corner=$(echo "$last_radio_line" | grep -oE '\[[a-z_]+\]' | tail -1 | tr -d '[]')
		fi

	# --- ラジオ deferred キュー状態 ---
	local radio_deferred_pending=0 radio_deferred_playing=0
	if [[ -d tmp/.radio_deferred_queue ]]; then
		radio_deferred_pending=$(find tmp/.radio_deferred_queue -name 'radio_*.txt' 2>/dev/null | wc -l | tr -d ' ')
		radio_deferred_playing=$(find tmp/.radio_deferred_queue -name 'radio_*.playing' 2>/dev/null | wc -l | tr -d ' ')
	fi

	# radio_state の "playing" は予約済み/待機中も含むため、実再生状況で補正
	local radio_effective_status="$radio_status"
	if [[ "$radio_effective_status" == "queued" ]] && ! $say_running && [[ "$say_effective_status" != "playing" ]] && (( radio_deferred_pending == 0 && radio_deferred_playing == 0 )); then
		radio_effective_status="idle"
		radio_elapsed=""
	fi
	if [[ "$radio_effective_status" == "playing" ]]; then
		if ! $say_running && [[ "$say_effective_status" != "playing" ]] && ! $say_source_is_radio && (( radio_deferred_pending == 0 && radio_deferred_playing == 0 )); then
			# audio_worker completion can leave .radio_state briefly at playing.
			# With no live radio source and no deferred item, show it as idle
			# instead of a misleading QUEUED timer.
			radio_effective_status="idle"
			radio_elapsed=""
		elif ! $say_running && [[ "$say_effective_status" != "playing" ]]; then
			radio_effective_status="queued"
		elif [[ -n "$say_phase" ]] && [[ "$say_phase" != "playing" ]]; then
			radio_effective_status="queued"
		elif [[ -n "$say_source" ]] && ! $say_source_is_radio; then
			radio_effective_status="queued"
		fi
	fi

	# --- コメントキュー状態 ---
	local comment_queue_pending=0 comment_queue_playing=0
	if [[ -d tmp/.comment_queue ]]; then
		comment_queue_pending=$(find tmp/.comment_queue -maxdepth 1 -type f -name '*.txt' ! -name 'played_hashes.txt' 2>/dev/null | wc -l | tr -d ' ')
		comment_queue_playing=$(find tmp/.comment_queue -maxdepth 1 -type f -name '*.playing' 2>/dev/null | wc -l | tr -d ' ')
	fi
	local comment_queue_count=$((comment_queue_pending + comment_queue_playing))
	local manual_audio_trigger_count=0
	if [[ -d tmp/.manual_audio_triggers ]]; then
		manual_audio_trigger_count=$(find tmp/.manual_audio_triggers -name '*.cmd' 2>/dev/null | wc -l | tr -d ' ')
	fi

	# コメント生成プロセス (PIDファイル + 状態ファイル)
	local comment_gen_running=false comment_gen_pid="" comment_gen_phase=""
	if [[ -f tmp/.twitch_chat/comment_gen.pid ]]; then
		comment_gen_pid=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null)
		comment_gen_pid=${comment_gen_pid%%|*}
		if [[ -n "$comment_gen_pid" ]] && _pid_exists "$comment_gen_pid"; then
			comment_gen_running=true
		fi
	fi
	if [[ -f $TMP_STATE_DIR/.comment_gen_state ]]; then
		local cg_line=$(cat $TMP_STATE_DIR/.comment_gen_state 2>/dev/null)
		comment_gen_phase="${cg_line%%:*}"
		local cg_ts=${cg_line##*:}
		if ! $comment_gen_running && [[ -n "$cg_ts" ]] && (( $(date +%s) - cg_ts < 300 )); then
			comment_gen_running=true
		fi
	fi

	# --- say_queue 内の再生待ち項目を集計 (_chunks.txt 除外, 5分以内のみ) ---
	local say_queue_waiting=0
	if [[ -d tmp/.say_queue ]]; then
		say_queue_waiting=$(find tmp/.say_queue -maxdepth 1 -name 'content_*.txt' ! -name '*_chunks.txt' -mmin -5 2>/dev/null | wc -l | tr -d ' ')
	fi

	# --- Worker プロセス状態 ---
	local chat_worker_running=false chat_worker_pid=""
	if [[ -f tmp/state/chat_worker.pid ]]; then
		chat_worker_pid=$(cat tmp/state/chat_worker.pid 2>/dev/null)
		if [[ -n "$chat_worker_pid" ]] && _pid_exists "$chat_worker_pid"; then
			chat_worker_running=true
		fi
	fi
	if ! $chat_worker_running; then
		chat_worker_pid=$(_find_process_pid '[/ ]workers/chat_worker[.]sh([[:space:]]|$)')
		if [[ -n "$chat_worker_pid" ]] && _pid_exists "$chat_worker_pid"; then
			chat_worker_running=true
		fi
	fi
	if ! $chat_worker_running && [[ -f tmp/state/chat_worker.pid ]] && _recent_file_active "tmp/state/chat_worker.pid" 30; then
		chat_worker_running=true
		chat_worker_pid=$(_activity_label "tmp/state/chat_worker.pid" "heartbeat")
	fi
	local youtube_worker_running=false youtube_worker_pid="" youtube_worker_enabled=false
	case "${YOUTUBE_CHAT_ENABLED:-0}" in
	1 | true | TRUE | yes | YES) youtube_worker_enabled=true ;;
	esac
	if [[ -f tmp/state/youtube_worker.pid ]]; then
		youtube_worker_pid=$(cat tmp/state/youtube_worker.pid 2>/dev/null)
		if [[ -n "$youtube_worker_pid" ]] && _pid_exists "$youtube_worker_pid"; then
			youtube_worker_running=true
		fi
	fi
	if ! $youtube_worker_running; then
		youtube_worker_pid=$(_find_process_pid '[/ ]workers/youtube_worker[.]sh([[:space:]]|$)')
		if [[ -n "$youtube_worker_pid" ]] && _pid_exists "$youtube_worker_pid"; then
			youtube_worker_running=true
		fi
	fi
	if ! $youtube_worker_running && [[ -f tmp/state/youtube_worker.pid ]] && _recent_file_active "tmp/state/youtube_worker.pid" 90; then
		youtube_worker_running=true
		youtube_worker_pid=$(_activity_label "tmp/state/youtube_worker.pid" "heartbeat")
	fi
	local audio_worker_running=false audio_worker_pid=""
	if [[ -f tmp/state/audio_worker.pid ]]; then
		audio_worker_pid=$(cat tmp/state/audio_worker.pid 2>/dev/null)
		if [[ -n "$audio_worker_pid" ]] && _pid_exists "$audio_worker_pid"; then
			audio_worker_running=true
		fi
	fi
	if ! $audio_worker_running; then
		audio_worker_pid=$(_find_process_pid '[/ ]workers/audio_worker[.]sh([[:space:]]|$)')
		if [[ -n "$audio_worker_pid" ]] && _pid_exists "$audio_worker_pid"; then
			audio_worker_running=true
		fi
	fi
	if ! $audio_worker_running && [[ -f tmp/state/audio_worker.pid ]] && _recent_file_active "tmp/state/audio_worker.pid" 30; then
		audio_worker_running=true
		audio_worker_pid=$(_activity_label "tmp/state/audio_worker.pid" "heartbeat")
	fi
	local radio_worker_running=false radio_worker_pid=""
	if [[ -f tmp/state/radio_worker.pid ]]; then
		radio_worker_pid=$(cat tmp/state/radio_worker.pid 2>/dev/null)
		if [[ -n "$radio_worker_pid" ]] && _pid_exists "$radio_worker_pid"; then
			radio_worker_running=true
		fi
	fi
	if ! $radio_worker_running; then
		radio_worker_pid=$(_find_process_pid '[/ ]workers/radio_worker[.]sh([[:space:]]|$)')
		if [[ -n "$radio_worker_pid" ]] && _pid_exists "$radio_worker_pid"; then
			radio_worker_running=true
		fi
	fi
	if ! $radio_worker_running && [[ -f tmp/state/radio_worker.pid ]] && _recent_file_active "tmp/state/radio_worker.pid" 30; then
		radio_worker_running=true
		radio_worker_pid=$(_activity_label "tmp/state/radio_worker.pid" "heartbeat")
	fi
	# durable pause markers (tmp/state/<name>.paused): worker stays alive but idle (park)
	local chat_worker_paused=false youtube_worker_paused=false audio_worker_paused=false radio_worker_paused=false
	[[ -f tmp/state/chat_worker.paused ]] && chat_worker_paused=true
	[[ -f tmp/state/youtube_worker.paused ]] && youtube_worker_paused=true
	[[ -f tmp/state/audio_worker.paused ]] && audio_worker_paused=true
	[[ -f tmp/state/radio_worker.paused ]] && radio_worker_paused=true
	local prediction_worker_running=false prediction_worker_pid="" prediction_worker_paused=false
	if [[ -f tmp/state/prediction_worker.paused ]]; then
		prediction_worker_paused=true
	fi
	if [[ -f tmp/state/prediction_worker.pid ]]; then
		prediction_worker_pid=$(cat tmp/state/prediction_worker.pid 2>/dev/null)
		if [[ -n "$prediction_worker_pid" ]] && _pid_exists "$prediction_worker_pid"; then
			prediction_worker_running=true
		fi
	fi
	if ! $prediction_worker_running; then
		prediction_worker_pid=$(_find_process_pid '[/ ]workers/prediction_worker[.]sh([[:space:]]|$)')
		if [[ -n "$prediction_worker_pid" ]] && _pid_exists "$prediction_worker_pid"; then
			prediction_worker_running=true
		fi
	fi
	local improve_daemon_running=false improve_daemon_pid=""
	if [[ -f tmp/state/improve_daemon.pid ]]; then
		improve_daemon_pid=$(cat tmp/state/improve_daemon.pid 2>/dev/null)
		if [[ -n "$improve_daemon_pid" ]] && _pid_exists "$improve_daemon_pid"; then
			improve_daemon_running=true
		fi
	fi
	if ! $improve_daemon_running; then
		improve_daemon_pid=$(_find_process_pid '[/ ]improve_daemon[.]sh([[:space:]]|$)')
		if [[ -n "$improve_daemon_pid" ]] && _pid_exists "$improve_daemon_pid"; then
			improve_daemon_running=true
		fi
	fi
	if ! $improve_daemon_running && [[ -f tmp/state/improve_daemon.pid ]] && _recent_file_active "tmp/state/improve_daemon.pid" 60; then
		improve_daemon_running=true
		improve_daemon_pid=$(_activity_label "tmp/state/improve_daemon.pid" "heartbeat")
	fi
	# --- Outbound chat queue ---
	local outbound_pending=0
	if [[ -d tmp/.outbound_chat_queue/pending ]]; then
		outbound_pending=$(find tmp/.outbound_chat_queue/pending -name '*.msg' 2>/dev/null | wc -l | tr -d ' ')
	fi
	local twitch_send_error=""
	if [[ -f tmp/debug/last_twitch_send_error.txt ]] && _recent_file_active "tmp/debug/last_twitch_send_error.txt" 900; then
		twitch_send_error=$(head -1 tmp/debug/last_twitch_send_error.txt 2>/dev/null | tr -d '\r' | cut -c1-100)
	fi
	local duplicate_status="" duplicate_detail=""
	if [[ -f tmp/state/worker_duplicates.json ]] && _recent_file_active "tmp/state/worker_duplicates.json" 120; then
		local duplicate_info=""
		duplicate_info=$(python3 - <<'PY' 2>/dev/null || true
import json
try:
    d = json.load(open("tmp/state/worker_duplicates.json", encoding="utf-8"))
    items = d.get("duplicates") or []
    status = str(d.get("status") or ("duplicate" if items else "ok"))
    if items:
        detail = "; ".join(f"{i.get('name')} x{i.get('count')} pids={','.join(map(str, i.get('pids') or []))}" for i in items)
    elif status not in ("ok", "duplicate"):
        detail = str(d.get("error") or "duplicate scan unavailable")
    else:
        detail = ""
    detail = detail.replace("\n", " ")[:120]
    print(f"{status}:{detail}")
except Exception:
    pass
PY
)
		if [[ -n "$duplicate_info" ]]; then
			duplicate_status="${duplicate_info%%:*}"
			duplicate_detail="${duplicate_info#*:}"
		fi
	fi
	if [[ -z "$duplicate_status" || "$duplicate_status" == "unknown" ]]; then
		local duplicate_fallback_info=""
		duplicate_fallback_info=$(_worker_duplicates_from_ps_fallback)
		if [[ -n "$duplicate_fallback_info" ]]; then
			duplicate_status="${duplicate_fallback_info%%:*}"
			duplicate_detail="${duplicate_fallback_info#*:}"
		fi
	fi

	# --- Twitch チャット状態 ---
	local twitch_running=false twitch_pid=""
	# chat_worker が動いていればそちらを優先
	if $chat_worker_running; then
		twitch_running=true
		twitch_pid="$chat_worker_pid"
	elif [[ -f tmp/.twitch_chat/daemon.pid ]]; then
		twitch_pid=$(cat tmp/.twitch_chat/daemon.pid 2>/dev/null)
		if [[ -n "$twitch_pid" ]] && _pid_exists "$twitch_pid"; then
			twitch_running=true
		fi
	fi

	# 未読コメント数
	local twitch_pending=0
	if [[ -f tmp/.twitch_chat/pending.log ]] && [[ -s tmp/.twitch_chat/pending.log ]]; then
		twitch_pending=$(wc -l < tmp/.twitch_chat/pending.log | tr -d ' ')
	fi
	local youtube_pending=0
	if [[ -f tmp/.youtube_chat/pending.log ]] && [[ -s tmp/.youtube_chat/pending.log ]]; then
		youtube_pending=$(wc -l < tmp/.youtube_chat/pending.log | tr -d ' ')
	fi
	local youtube_error=""
	local youtube_send_error=""
	if [[ -s tmp/.youtube_chat/last_error.txt ]] && _recent_file_active "tmp/.youtube_chat/last_error.txt" 300; then
		youtube_error=$(head -1 tmp/.youtube_chat/last_error.txt 2>/dev/null)
	fi
	if [[ -s tmp/.youtube_chat/last_send_error.txt ]] && _recent_file_active "tmp/.youtube_chat/last_send_error.txt" 300; then
		youtube_send_error=$(head -1 tmp/.youtube_chat/last_send_error.txt 2>/dev/null)
	fi

	# 最新コメント
	local twitch_latest=""
	if [[ -f tmp/twitch_comments.txt ]] && [[ -s tmp/twitch_comments.txt ]]; then
		twitch_latest=$(tail -1 tmp/twitch_comments.txt)
		# "    ▸ Latest     " = 18 → text max = W-18
		local max_tw=$(( W - 18 ))
		(( ${#twitch_latest} > max_tw )) && twitch_latest="${twitch_latest[1,$((max_tw-3))]}..."
	fi
	local youtube_latest=""
	if [[ -f tmp/youtube_comments.txt ]] && [[ -s tmp/youtube_comments.txt ]]; then
		youtube_latest=$(tail -1 tmp/youtube_comments.txt)
		local max_yt=$(( W - 18 ))
		(( ${#youtube_latest} > max_yt )) && youtube_latest="${youtube_latest[1,$((max_yt-3))]}..."
	fi

	# 最新ドロップ
	local latest_drop=""
	latest_drop=$(_latest_drop_summary)
	[[ -n "$latest_drop" ]] || latest_drop="(no drop log)"

	# ========== 描画 ==========
	echo ""
	printf "${C_BOLD}${C_CYAN}━━━ SOREN STATUS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"
	echo ""

	# === セクション: Core ===
	printf "  ${C_BOLD}CORE${C_RESET}\n"

	# メインループ
	if $loop_running; then
		if [[ "$loop_pid" == activity:* ]]; then
			printf "    ${C_GREEN}●${C_RESET} Loop        ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${loop_pid#activity:}"
		else
			printf "    ${C_GREEN}●${C_RESET} Loop        ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=${loop_pid}${C_RESET}\n"
		fi
	else
		printf "    ${C_RED}○${C_RESET} Loop        ${C_DIM}STOPPED${C_RESET}\n"
	fi

	# Worker 個別状態
	local _worker_rows=(
		"ChatW" "$chat_worker_running" "$chat_worker_pid"
		"YouTubeW" "$youtube_worker_running" "$youtube_worker_pid"
		"AudioW" "$audio_worker_running" "$audio_worker_pid"
		"RadioW" "$radio_worker_running" "$radio_worker_pid"
		"PredW" "$prediction_worker_running" "$prediction_worker_pid"
		"ImproveD" "$improve_daemon_running" "$improve_daemon_pid"
	)
	local _w_i _w_name _w_running _w_pid
	for ((_w_i = 1; _w_i <= ${#_worker_rows[@]}; _w_i += 3)); do
		_w_name="${_worker_rows[$_w_i]}"
		_w_running="${_worker_rows[$((_w_i + 1))]}"
		_w_pid="${_worker_rows[$((_w_i + 2))]}"
		local _w_paused=false
		case "$_w_name" in
		ChatW) _w_paused=$chat_worker_paused ;;
		YouTubeW) _w_paused=$youtube_worker_paused ;;
		AudioW) _w_paused=$audio_worker_paused ;;
		RadioW) _w_paused=$radio_worker_paused ;;
		PredW) _w_paused=$prediction_worker_paused ;;
		esac
		if [[ "$_w_paused" == "true" ]]; then
			printf "    ${C_YELLOW}◌${C_RESET} %-11s ${C_YELLOW}PAUSED${C_RESET}  ${C_DIM}idle — rm tmp/state/*.paused to resume${C_RESET}\n" "$_w_name"
		elif [[ "$_w_running" == "true" ]]; then
			if [[ "$_w_pid" == activity:* ]]; then
				printf "    ${C_GREEN}●${C_RESET} %-11s ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$_w_name" "${_w_pid#activity:}"
			else
				printf "    ${C_GREEN}●${C_RESET} %-11s ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=%s${C_RESET}\n" "$_w_name" "$_w_pid"
			fi
		elif [[ "$_w_name" == "YouTubeW" && "$youtube_worker_enabled" != "true" ]]; then
			printf "    ${C_DIM}○${C_RESET} %-11s ${C_DIM}DISABLED${C_RESET}  ${C_DIM}YOUTUBE_CHAT_ENABLED=0${C_RESET}\n" "$_w_name"
		else
			printf "    ${C_RED}○${C_RESET} %-11s ${C_DIM}STOPPED${C_RESET}\n" "$_w_name"
		fi
	done

	# ワーカー稼働メーター
	local workers_online=0 workers_total=6 workers_expected=6
	# paused workers (tmp/state/<name>.paused) are intentionally idle → drop from expected & online
	$prediction_worker_paused && workers_expected=$((workers_expected - 1))
	$chat_worker_paused && workers_expected=$((workers_expected - 1))
	$audio_worker_paused && workers_expected=$((workers_expected - 1))
	$radio_worker_paused && workers_expected=$((workers_expected - 1))
	$youtube_worker_enabled && workers_expected=$((workers_expected + 1))
	{ $youtube_worker_enabled && $youtube_worker_paused; } && workers_expected=$((workers_expected - 1))
	$loop_running && workers_online=$((workers_online + 1))
	{ $chat_worker_running && ! $chat_worker_paused; } && workers_online=$((workers_online + 1))
	{ $youtube_worker_running && ! $youtube_worker_paused; } && workers_online=$((workers_online + 1))
	{ $audio_worker_running && ! $audio_worker_paused; } && workers_online=$((workers_online + 1))
	{ $radio_worker_running && ! $radio_worker_paused; } && workers_online=$((workers_online + 1))
	{ $prediction_worker_running && ! $prediction_worker_paused; } && workers_online=$((workers_online + 1))
	$improve_daemon_running && workers_online=$((workers_online + 1))
	local workers_bar
	workers_bar=$(_bar_meter "$workers_online" "$workers_expected" 12)
	printf "    ${C_WHITE}▸${C_RESET} Workers     ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d/%d expected online${C_RESET}\n" "$workers_bar" "$workers_online" "$workers_expected"
	if [[ "$duplicate_status" == "duplicate" ]]; then
		printf "    ${C_RED}!${C_RESET} Duplicates  ${C_RED}DETECTED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$duplicate_detail"
	elif [[ "$duplicate_status" == "ok" ]]; then
		printf "    ${C_GREEN}✓${C_RESET} Duplicates  ${C_DIM}none${C_RESET}\n"
	elif [[ "$duplicate_status" == "unknown" ]]; then
		printf "    ${C_YELLOW}!${C_RESET} Duplicates  ${C_YELLOW}UNKNOWN${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${duplicate_detail:-duplicate scan unavailable}"
	fi
	local stream_status_line stream_status stream_detail
	case "$STREAM_BACKEND" in
	ffmpeg)
		stream_status_line="$(_direct_stream_status_line)"
		stream_status="${stream_status_line%%|*}"
		stream_detail="${stream_status_line#*|}"
		case "$stream_status" in
		ok)
			printf "    ${C_GREEN}●${C_RESET} Backend     ${C_GREEN}FFMPEG LIVE${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$stream_detail"
			;;
		down)
			printf "    ${C_RED}!${C_RESET} Backend     ${C_RED}FFMPEG DOWN${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$stream_detail"
			;;
		*)
			printf "    ${C_YELLOW}!${C_RESET} Backend     ${C_YELLOW}FFMPEG UNKNOWN${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${stream_detail:-status unavailable}"
			;;
		esac
		local soak_status_line soak_status soak_detail
		soak_status_line="$(_direct_soak_status_line)"
		soak_status="${soak_status_line%%|*}"
		soak_detail="${soak_status_line#*|}"
		case "$soak_status" in
		running) printf "    ${C_CYAN}◉${C_RESET} Soak        ${C_CYAN}RUNNING${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$soak_detail" ;;
		passed) printf "    ${C_GREEN}✓${C_RESET} Soak        ${C_GREEN}PASSED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$soak_detail" ;;
		failed) printf "    ${C_RED}!${C_RESET} Soak        ${C_RED}FAILED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$soak_detail" ;;
		not_started) printf "    ${C_DIM}○${C_RESET} Soak        ${C_DIM}NOT STARTED${C_RESET}\n" ;;
		*) printf "    ${C_YELLOW}!${C_RESET} Soak        ${C_YELLOW}%s${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${soak_status:u}" "$soak_detail" ;;
		esac
		local av_status_line av_status av_detail
		av_status_line="$(_direct_av_sync_status_line)"
		av_status="${av_status_line%%|*}"
		av_detail="${av_status_line#*|}"
		case "$av_status" in
		passed) printf "    ${C_GREEN}✓${C_RESET} AVSync      ${C_GREEN}PASSED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$av_detail" ;;
		failed) printf "    ${C_RED}!${C_RESET} AVSync      ${C_RED}FAILED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$av_detail" ;;
		not_started) printf "    ${C_DIM}○${C_RESET} AVSync      ${C_DIM}NOT TESTED${C_RESET}\n" ;;
		*) printf "    ${C_YELLOW}!${C_RESET} AVSync      ${C_YELLOW}%s${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${av_status:u}" "$av_detail" ;;
		esac
		;;
	obs)
		printf "    ${C_GREEN}▸${C_RESET} Backend     ${C_GREEN}OBS${C_RESET}\n"
		stream_status_line="$(_obs_status_line)"
		stream_status="${stream_status_line%%|*}"
		stream_detail="${stream_status_line#*|}"
		case "$stream_status" in
		ok)
			printf "    ${C_GREEN}●${C_RESET} OBSWS       ${C_GREEN}OK${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$stream_detail"
			;;
		safe)
			printf "    ${C_YELLOW}!${C_RESET} OBSWS       ${C_YELLOW}SAFE MODE${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$stream_detail"
			;;
		down)
			printf "    ${C_RED}!${C_RESET} OBSWS       ${C_RED}DOWN${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$stream_detail"
			;;
		*)
			printf "    ${C_YELLOW}!${C_RESET} OBSWS       ${C_YELLOW}UNKNOWN${C_RESET}  ${C_DIM}%s${C_RESET}\n" "${stream_detail:-status unavailable}"
			;;
		esac
		;;
	*)
		printf "    ${C_RED}!${C_RESET} Backend     ${C_RED}INVALID${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$STREAM_BACKEND"
		;;
	esac
	if ! $improve_daemon_running && (( acc_count >= min_games )); then
		printf "    ${C_RED}!${C_RESET} ImproveD    ${C_RED}RESTART REQUIRED${C_RESET}  ${C_DIM}lock/improve gate reached${C_RESET}\n"
	elif ! $improve_daemon_running && (( min_games - acc_count <= 2 )); then
		printf "    ${C_YELLOW}!${C_RESET} ImproveD    ${C_YELLOW}restart soon${C_RESET}  ${C_DIM}%d games to improve gate${C_RESET}\n" "$(( min_games - acc_count ))"
	fi

		# 蓄積ゲーム (最低試合ゲート付き)
		if (( acc_count > 0 )); then
			local gate_color="$C_MAGENTA"
		(( acc_count >= min_games )) && gate_color="$C_GREEN"
		local count_label="${acc_count}試合目 (games)"
		local nation_label="R${acc_russia_count:-0}"
		$acc_soviet && nation_label="${nation_label} S=1"
		local max_scores=$(( W - 26 - ${#count_label} - ${#nation_label} ))
		(( max_scores < 8 )) && max_scores=8
		local scores_display="${acc_scores}"
		scores_display=$(_truncate_display_width_keep_tail "$scores_display" "$max_scores")
		printf "    ${gate_color}◆${C_RESET} Game        ${gate_color}%s${C_RESET}  ${C_DIM}%s [%s]${C_RESET}\n" "${count_label}" "$nation_label" "${scores_display}"
	fi

	# キュー負荷メーター（show_status_g にはない運用系指標）
	local queue_total=$(( acc_count + comment_queue_count + twitch_pending ))
	local queue_bar
	queue_bar=$(_bar_meter "$queue_total" 30 12)
	printf "    ${C_BLUE}▸${C_RESET} QueueMeter  ${C_DIM}[%s]${C_RESET}  ${C_DIM}A=%d C=%d T=%d${C_RESET}\n" \
		"$queue_bar" "$acc_count" "$comment_queue_count" "$twitch_pending"
	if [[ -n "$ai_backoff_lines" ]]; then
		local ai_backoff_line ai_backoff_index=0
		while IFS= read -r ai_backoff_line; do
			[ -n "$ai_backoff_line" ] || continue
			if (( ai_backoff_index == 0 )); then
				printf "    ${C_RED}!${C_RESET} AI 429      ${C_RED}%s${C_RESET}\n" "$ai_backoff_line"
			else
				printf "      ${C_RED}↳${C_RESET}            ${C_RED}%s${C_RESET}\n" "$ai_backoff_line"
			fi
			ai_backoff_index=$((ai_backoff_index + 1))
		done <<< "$ai_backoff_lines"
	fi

	local max_drop=$(( W - 18 ))
	latest_drop=$(_truncate_display_width "$latest_drop" "$max_drop")
	printf "    ${C_CYAN}▾${C_RESET} LastDrop   ${C_DIM}%s${C_RESET}\n" "$latest_drop"

	# リバート・リジェクト情報
	if $revert_available || (( rejected_count > 0 )); then
		local revert_info=""
		$revert_available && revert_info="${C_DIM}revert=ready${C_RESET}"
		local reject_info=""
		(( rejected_count > 0 )) && reject_info="  ${C_DIM}rejected=${rejected_count}${C_RESET}"
		printf "    ${C_DIM}▸${C_RESET} Safety      ${revert_info}${reject_info}\n"
	fi

	local d_flag="off" t_flag="off" w_flag="off" escape_color="$C_DIM"
	[[ "$DIVERSITY_PREMIUM_ENABLED" == "1" ]] && d_flag="on"
	[[ "$TABU_ENABLED" == "1" ]] && t_flag="on"
	[[ "$WILDCARD_ENABLED" == "1" ]] && w_flag="on"
	if [[ "$WILDCARD_ENABLED" == "1" ]]; then
		escape_color="$C_GREEN"
		(( stagnation_count >= WILDCARD_TRIGGER_STAGNATION )) && escape_color="$C_YELLOW"
		(( regression_streak >= ${WILDCARD_REGRESSION_STREAK:-2} )) && escape_color="$C_YELLOW"
	fi
		local stagnation_detail="stag=${stagnation_count}/${WILDCARD_TRIGGER_STAGNATION} reg=${regression_streak}/${WILDCARD_REGRESSION_STREAK:-2}"
		[[ -n "$stagnation_defer_label" ]] && stagnation_detail="${stagnation_defer_label} ${stagnation_detail}"
		stagnation_detail="${stagnation_detail} ${stagnation_event} ${stagnation_age} ago wc=${wildcard_origin_count}"
		printf "    ${C_MAGENTA}◇${C_RESET} Escape      ${escape_color}D=%s T=%s W=%s${C_RESET}  ${C_DIM}%s${C_RESET}\n" \
			"$d_flag" "$t_flag" "$w_flag" "$stagnation_detail"
		if [[ "$wildcard_eval_label" != "none" ]]; then
			printf "    ${C_MAGENTA}▸${C_RESET} %-11s ${C_DIM}%s${C_RESET}\n" "$wildcard_eval_name" "$wildcard_eval_label"
		fi
		if [[ "$annealing_label" != "none" ]]; then
			printf "    ${C_BLUE}▸${C_RESET} AnnealObs   ${C_DIM}%s${C_RESET}\n" "$annealing_label"
		fi
		if [[ "$archive_next_label" != "none" ]]; then
			printf "    ${C_YELLOW}▸${C_RESET} ArchiveNext ${C_YELLOW}%s${C_RESET}\n" "$archive_next_label"
		fi
		if [[ "$fresh_objective_label" != "none" ]]; then
			local fresh_objective_display="$fresh_objective_label"
			fresh_objective_display=$(_truncate_display_width "$fresh_objective_display" "$(( W - 18 ))")
			printf "    ${C_YELLOW}▸${C_RESET} FreshObj   ${C_YELLOW}%s${C_RESET}\n" "$fresh_objective_display"
		fi
		if [[ -n "$wildcard_parallel_label" ]]; then
			printf "    ${C_YELLOW}▸${C_RESET} %-11s ${C_YELLOW}%s${C_RESET}\n" "$wildcard_parallel_name" "$wildcard_parallel_label"
		fi
			if [[ "$viewer_chat_label" != "none" ]]; then
				printf "    ${C_CYAN}▸${C_RESET} ChatObs     ${C_DIM}%s${C_RESET}\n" "$viewer_chat_label"
			fi
			if [[ "$improve_backoff_label" != "none" ]]; then
				printf "    ${C_YELLOW}▸${C_RESET} ImproveBack ${C_YELLOW}%s${C_RESET}\n" "$improve_backoff_label"
			fi
			if [[ "$soren91_improve_watchdog_label" != "none" ]]; then
				printf "    ${C_YELLOW}▸${C_RESET} S91Improve  ${C_YELLOW}%s${C_RESET}\n" "$soren91_improve_watchdog_label"
			fi

			echo ""

	# === セクション: Audio ===
	printf "  ${C_BOLD}AUDIO${C_RESET}\n"

	# TTS (say)
		if [[ "$say_effective_status" == "playing" ]]; then
			printf "    ${C_GREEN}♪${C_RESET} Say         ${C_GREEN}PLAYING${C_RESET}  ${C_DIM}PID=${say_pid}${C_RESET}"
			if [[ -n "$say_source" ]]; then
				local say_kind="other"
				local say_kind_label=""
				$say_source_is_radio && say_kind="radio"
				$say_source_is_comment && say_kind="comment"
				if [[ -n "$say_label" ]]; then
					say_kind_label="$say_label"
				else
					say_kind_label="$say_kind"
				fi
				printf "  ${C_DIM}[%s:%s]${C_RESET}" "$say_kind_label" "${say_phase:-playing}"
			fi
			echo ""
		elif [[ "$say_effective_status" == "preparing" ]]; then
			printf "    ${C_CYAN}♪${C_RESET} Say         ${C_CYAN}PREPARING${C_RESET}"
			if [[ -n "$say_source" ]]; then
				local prep_label="${say_label:-${say_phase:-?}}"
				printf "  ${C_DIM}[%s:%s:%s]${C_RESET}" "$prep_label" "${say_phase:-?}" "${say_source_age:-?}"
			fi
			echo ""
		elif [[ "$say_effective_status" == "waiting" ]]; then
			printf "    ${C_BLUE}♪${C_RESET} Say         ${C_BLUE}WAITING${C_RESET}"
			if [[ -n "$say_source" ]]; then
				local wait_label="${say_label:-${say_phase:-?}}"
				printf "  ${C_DIM}[%s:%s:%s]${C_RESET}" "$wait_label" "${say_phase:-?}" "${say_source_age:-?}"
			fi
			echo ""
		elif [[ "$say_effective_status" == "retry" ]]; then
			printf "    ${C_YELLOW}♪${C_RESET} Say         ${C_YELLOW}RETRY${C_RESET}"
			if [[ -n "$say_source" ]]; then
				local retry_label="${say_label:-${say_phase:-?}}"
				printf "  ${C_DIM}[%s:%s:%s]${C_RESET}" "$retry_label" "${say_phase:-?}" "${say_source_age:-?}"
			fi
			echo ""
		else
			printf "    ${C_DIM}♪${C_RESET} Say         ${C_DIM}SILENT${C_RESET}"
			$say_lock_stale && printf "  ${C_YELLOW}[stale-lock:%s]${C_RESET}" "${say_lock_age:-?}"
			if [[ -n "$say_source" ]]; then
				local last_label="${say_label:-${say_phase:-?}}"
				printf "  ${C_YELLOW}[last:%s:%s:%s]${C_RESET}" "$last_label" "${say_phase:-?}" "${say_source_age:-?}"
			fi
			echo ""
		fi

	# VOICEVOX 合成状態
		if $voicevox_synth_locked; then
			if $stream_active; then
				printf "    ${C_CYAN}🔊${C_RESET} VOICEVOX    ${C_CYAN}SYNTH${C_RESET}  ${C_DIM}streaming${C_RESET}"
				if (( stream_chunk_total > 0 )); then
					printf " ${C_DIM}chunk %d/%d${C_RESET}" "$((stream_chunk_done + 1))" "$stream_chunk_total"
				fi
				echo ""
			else
				printf "    ${C_CYAN}🔊${C_RESET} VOICEVOX    ${C_CYAN}SYNTH${C_RESET}  ${C_DIM}locked${C_RESET}\n"
			fi
		fi

	# ラジオコーナー
	local corner_label="${radio_corner:-?}"
	local elapsed_label=""
	[[ -n "$radio_elapsed" ]] && elapsed_label=" ${radio_elapsed}"
	case "$radio_effective_status" in
		playing)
			printf "    ${C_GREEN}📻${C_RESET} Radio       ${C_GREEN}PLAYING${C_RESET}  ${C_DIM}[${corner_label}]${elapsed_label}${C_RESET}\n"
			;;
		queued)
			printf "    ${C_YELLOW}📻${C_RESET} Radio       ${C_YELLOW}QUEUED${C_RESET}  ${C_DIM}[${corner_label}]${elapsed_label}${C_RESET}\n"
			;;
		verifying)
			printf "    ${C_MAGENTA}📻${C_RESET} Radio       ${C_MAGENTA}VERIFYING${C_RESET}  ${C_DIM}[${corner_label}]${elapsed_label}${C_RESET}\n"
			;;
		generating)
			printf "    ${C_CYAN}📻${C_RESET} Radio       ${C_CYAN}GENERATING${C_RESET}  ${C_DIM}[${corner_label}]${elapsed_label}${C_RESET}\n"
			;;
		*)
			if [[ -n "$radio_corner" ]]; then
				printf "    ${C_DIM}📻${C_RESET} Radio       ${C_DIM}IDLE${C_RESET}  ${C_DIM}last=[${corner_label}]${C_RESET}\n"
			else
				printf "    ${C_DIM}📻${C_RESET} Radio       ${C_DIM}IDLE${C_RESET}\n"
			fi
			;;
	esac

	# コメント パイプライン表示
	# 生成 → キュー待ち → 再生中 の流れ
	if $comment_gen_running; then
		local gen_label="${comment_gen_phase:-generating}"
		printf "    ${C_YELLOW}⟳${C_RESET} CommentGen  ${C_YELLOW}%s${C_RESET}" "${gen_label}"
		[[ -n "$comment_gen_pid" ]] && printf "  ${C_DIM}PID=${comment_gen_pid}${C_RESET}"
		echo ""
	else
		printf "    ${C_DIM}⟳${C_RESET} CommentGen  ${C_DIM}idle${C_RESET}\n"
	fi
	if (( comment_queue_pending > 0 || comment_queue_playing > 0 )); then
		local cq_parts=""
		(( comment_queue_playing > 0 )) && cq_parts="${C_GREEN}${comment_queue_playing} playing${C_RESET}"
		if (( comment_queue_pending > 0 )); then
			[[ -n "$cq_parts" ]] && cq_parts="${cq_parts} ${C_DIM}|${C_RESET} "
			cq_parts="${cq_parts}${C_MAGENTA}${comment_queue_pending} queued${C_RESET}"
		fi
		printf "    ${C_MAGENTA}💬${C_RESET} CommentQ    ${cq_parts}\n"
	else
		printf "    ${C_DIM}💬${C_RESET} CommentQ    ${C_DIM}empty${C_RESET}\n"
	fi

	# ラジオ deferred キュー
	if (( radio_deferred_playing > 0 || radio_deferred_pending > 0 )); then
		local rq_parts=""
		(( radio_deferred_playing > 0 )) && rq_parts="${C_GREEN}${radio_deferred_playing} playing${C_RESET}"
		if (( radio_deferred_pending > 0 )); then
			[[ -n "$rq_parts" ]] && rq_parts="${rq_parts} ${C_DIM}|${C_RESET} "
			rq_parts="${rq_parts}${C_CYAN}${radio_deferred_pending} queued${C_RESET}"
		fi
		printf "    ${C_CYAN}📻${C_RESET} RadioQ      ${rq_parts}\n"
	fi

	# say キュー（合成済み再生待ち）
	if (( say_queue_waiting > 1 )); then
		printf "    ${C_BLUE}▶${C_RESET} PlaybackQ   ${C_BLUE}$((say_queue_waiting - 1)) waiting${C_RESET}\n"
	fi

	if (( manual_audio_trigger_count > 0 )); then
		printf "    ${C_CYAN}⌘${C_RESET} TriggerQ    ${C_CYAN}${manual_audio_trigger_count} pending${C_RESET}\n"
	fi

	echo ""

	# === セクション: Twitch ===
	printf "  ${C_BOLD}TWITCH${C_RESET}\n"

	if $twitch_running; then
		printf "    ${C_GREEN}●${C_RESET} Chat        ${C_GREEN}CONNECTED${C_RESET}  ${C_DIM}PID=${twitch_pid}${C_RESET}\n"
	else
		printf "    ${C_RED}○${C_RESET} Chat        ${C_DIM}DISCONNECTED${C_RESET}\n"
	fi

	if (( twitch_pending > 0 )); then
		printf "    ${C_MAGENTA}▸${C_RESET} Pending     ${C_MAGENTA}${twitch_pending} comments${C_RESET}\n"
	fi

	if (( outbound_pending > 0 )); then
		printf "    ${C_CYAN}▸${C_RESET} OutboundQ   ${C_CYAN}${outbound_pending} messages${C_RESET}\n"
	fi

	if [[ -n "$twitch_send_error" ]]; then
		printf "    ${C_YELLOW}⚠${C_RESET} OutboundErr ${C_YELLOW}%s${C_RESET}\n" "$twitch_send_error"
	fi

	if [[ -n "$twitch_latest" ]]; then
		printf "    ${C_DIM}▸ Latest     ${twitch_latest}${C_RESET}\n"
	fi

	echo ""

	# === セクション: YouTube ===
	printf "  ${C_BOLD}YOUTUBE${C_RESET}\n"

	if $youtube_worker_enabled; then
		if $youtube_worker_running; then
			if [[ -n "$youtube_error" ]]; then
				(( ${#youtube_error} > 48 )) && youtube_error="${youtube_error:0:45}..."
				printf "    ${C_YELLOW}●${C_RESET} Chat        ${C_YELLOW}DEGRADED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$youtube_error"
			else
				printf "    ${C_GREEN}●${C_RESET} Chat        ${C_GREEN}CONNECTED${C_RESET}  ${C_DIM}PID=${youtube_worker_pid}${C_RESET}\n"
			fi
		else
			printf "    ${C_RED}○${C_RESET} Chat        ${C_DIM}DISCONNECTED${C_RESET}\n"
		fi
	else
		printf "    ${C_DIM}○${C_RESET} Chat        ${C_DIM}DISABLED${C_RESET}\n"
	fi

	if [[ "${YOUTUBE_CHAT_SEND_ENABLED:-0}" == "1" && -n "$youtube_send_error" ]]; then
		(( ${#youtube_send_error} > 48 )) && youtube_send_error="${youtube_send_error:0:45}..."
		printf "    ${C_YELLOW}▸${C_RESET} Send        ${C_YELLOW}DEGRADED${C_RESET}  ${C_DIM}%s${C_RESET}\n" "$youtube_send_error"
	fi

	if (( youtube_pending > 0 )); then
		printf "    ${C_MAGENTA}▸${C_RESET} Pending     ${C_MAGENTA}${youtube_pending} comments${C_RESET}\n"
	fi

	if [[ -n "$youtube_latest" ]]; then
		printf "    ${C_DIM}▸ Latest     ${youtube_latest}${C_RESET}\n"
	fi

	echo ""

	# === セクション: Rollback history ===
	printf "  ${C_BOLD}ROLLBACKS${C_RESET}\n"

	local rollback_head="total=${rollback_total}  rejected=${rejected_count}"
	if [[ -n "$rollback_last_age" ]]; then
		rollback_head="${rollback_head}  last=${rollback_last_age}"
	fi
	printf "    ${C_WHITE}▸${C_RESET} Rollbacks   ${C_DIM}%s${C_RESET}\n" "$rollback_head"

	if (( rollback_total > 0 )) && (( ${#rollback_events[@]} > 0 )); then
		local rb_idx=1
		local rb_when="" rb_from="" rb_to="" rb_target=""
		for rb_event in "${rollback_events[@]}"; do
			IFS='|' read -r rb_when rb_from rb_to rb_target <<<"$rb_event"
			local rb_when_compact="${rb_when//-//}"
			local rb_line="${rb_when_compact} ${rb_from}->${rb_to}"
			local max_rb=$(( W - 18 ))
			(( ${#rb_line} > max_rb )) && rb_line="${rb_line[1,$((max_rb-2))]}.."
			printf "    ${C_WHITE}▸${C_RESET} RB%-2d       ${C_DIM}%s${C_RESET}\n" "$rb_idx" "$rb_line"
			rb_idx=$((rb_idx + 1))
		done
		else
			printf "    ${C_WHITE}▸${C_RESET} RB History   ${C_DIM}(none)${C_RESET}\n"
		fi

		if [[ "$imp_status" != "idle" ]]; then
			echo ""
			printf "  ${C_BOLD}AI IMPROVE${C_RESET}\n"
			local imp_phase_label="${imp_phase:-running}"
			imp_phase_label=${imp_phase_label//_/ }
			if [[ "$imp_status" == "running" ]] && $imp_alive; then
				printf "    ${C_YELLOW}⟳${C_RESET} Improve     ${C_YELLOW}RUNNING${C_RESET}  ${C_DIM}PID=${imp_pid}${C_RESET}"
				[[ -n "$imp_elapsed" ]] && printf "  ${C_DIM}${imp_elapsed}${C_RESET}"
				printf "  ${C_DIM}[%d%% %s]${C_RESET}" "${imp_progress:-0}" "${imp_phase_label}"
				echo ""
				local imp_bar
				imp_bar=$(_bar_meter "${imp_progress:-0}" 100 12)
				printf "    ${C_WHITE}▸${C_RESET} ImproveProg ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d%%${C_RESET}\n" "$imp_bar" "${imp_progress:-0}"
			if [[ -n "$imp_ai_source" ]]; then
				local src_display="$imp_ai_source"
				local max_src=$(( W - 20 ))
				src_display=$(_truncate_display_width "$src_display" "$max_src")
				printf "    ${C_WHITE}▸${C_RESET} AIEngine    ${C_DIM}%s${C_RESET}\n" "$src_display"
			fi
			elif [[ "$imp_status" == "running" ]] && ! $imp_alive && $imp_state_activity_fresh; then
				printf "    ${C_YELLOW}⟳${C_RESET} Improve     ${C_YELLOW}RUNNING${C_RESET}  ${C_DIM}PID=%s not visible, log fresh %ss [%d%% %s]${C_RESET}\n" "$imp_pid" "${imp_monitor_stale_sec:-0}" "${imp_progress:-0}" "${imp_phase_label}"
				local imp_bar
				imp_bar=$(_bar_meter "${imp_progress:-0}" 100 12)
				printf "    ${C_WHITE}▸${C_RESET} ImproveProg ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d%%${C_RESET}\n" "$imp_bar" "${imp_progress:-0}"
					if [[ -n "$imp_ai_source" ]]; then
						local src_display="$imp_ai_source"
						local max_src=$(( W - 20 ))
						src_display=$(_truncate_display_width "$src_display" "$max_src")
						printf "    ${C_WHITE}▸${C_RESET} AIEngine    ${C_DIM}%s${C_RESET}\n" "$src_display"
					fi
				elif [[ "$imp_status" == "running" ]] && ! $imp_alive; then
					printf "    ${C_RED}✗${C_RESET} Improve     ${C_RED}STALE${C_RESET}  ${C_DIM}(PID=${imp_pid} dead, %d%% %s)${C_RESET}\n" "${imp_progress:-0}" "${imp_phase_label}"
					if [[ -n "$imp_ai_source" ]]; then
						local src_display="$imp_ai_source"
						local max_src=$(( W - 20 ))
						src_display=$(_truncate_display_width "$src_display" "$max_src")
						printf "    ${C_WHITE}▸${C_RESET} AIEngine    ${C_DIM}%s${C_RESET}\n" "$src_display"
					fi
				fi
			if [[ -n "$imp_ai_output_block" ]]; then
				_print_ai_output_lines "$imp_ai_output_block" "$imp_ai_age"
			fi
		fi

		echo ""
		printf "${C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"
	echo ""
}

#=== 描画ヘルパー: 各行に行末クリアを付与 ===
CLR=$'\033[K'

_clip_status_width() {
	python3 -c '
import re
import sys
import unicodedata

max_width = int(sys.argv[1])
ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def ch_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1

def clip_line(line):
    out = []
    width = 0
    i = 0
    truncated = False
    while i < len(line):
        m = ansi_re.match(line, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        ch = line[i]
        w = ch_width(ch)
        if width + w > max_width:
            truncated = True
            break
        out.append(ch)
        width += w
        i += 1
    if truncated and "\x1b[" in line:
        out.append("\x1b[0m")
    return "".join(out)

for line in sys.stdin.read().splitlines():
    print(clip_line(line))
' "$W"
}

render() {
	local raw="" clipped="" buf=""
	while IFS= read -r line; do
		raw+="${line}"$'\n'
	done
	clipped=$(printf '%s' "$raw" | _clip_status_width)
	while IFS= read -r line; do
		buf+="${line}${CLR}"$'\n'
	done <<<"$clipped"
	printf '\033[H%s\033[J' "$buf"
}

_render_status_once() {
	show_status | render
}

_wait_for_status_update() {
	local last_drop_sig="$1"
	local deadline=$(( $(date +%s) + WATCH_INTERVAL ))
	local current_drop_sig=""

	while (( $(date +%s) < deadline )); do
		sleep "$DROP_REFRESH_INTERVAL"
		current_drop_sig=$(_latest_drop_signature)
		[[ "$current_drop_sig" != "$last_drop_sig" ]] && return 0
	done
	return 0
}

#=== 実行 ===
printf '\033[?25l'          # カーソル非表示
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
printf '\033[2J'            # 初回だけ画面クリア
if [[ "$SHOW_STATUS_ONCE" == "1" ]]; then
	_render_status_once
	exit 0
fi
while true; do
	current_drop_sig=$(_latest_drop_signature)
	_render_status_once
	if _maybe_run_fullscreen_random; then
		continue
	fi
	_wait_for_status_update "$current_drop_sig"
done
