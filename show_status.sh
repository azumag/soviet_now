#!/bin/zsh
# show_status.sh - eloop 全体のステータス表示
#
# Usage: ./show_status.sh        # 10秒間隔で常時表示
#        ./show_status.sh 3      # 3秒間隔で常時表示
#        ./show_status.sh --once # 1回だけ表示して終了（確認・自動監視用）

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

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
	case "$err" in
	*"Operation not permitted"*|*"operation not permitted"*) return 0 ;;
	esac
	return 1
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
	ps -Ao pid=,command= 2>/dev/null | awk -v pattern="$pattern" -v self="$$" '
		$1 == self { next }
		$0 ~ pattern && $0 !~ /awk -v pattern/ {
			print $1
			exit
		}
	'
}

# ファイルの経過時間を返す
_file_age() {
	local f="$1"
	[[ -f "$f" ]] || return
	local mod=$(stat -f '%m' "$f" 2>/dev/null)
	[[ -n "$mod" ]] || return
	local age=$(( $(date +%s) - mod ))
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
	local imp_status="idle" imp_pid=0 imp_hash="" imp_phase="" imp_progress=0
	if [[ -f "$TMP_STATE_DIR/improve_state.json" ]]; then
		eval $(python3 -c "
import json, shlex
d=json.load(open('$TMP_STATE_DIR/improve_state.json'))
print('imp_status=' + shlex.quote(str(d.get('status', 'idle'))))
print(f'imp_pid={d.get(\"pid\",0)}')
print('imp_hash=' + shlex.quote(str(d.get('strategy_hash_before', ''))))
print('imp_phase=' + shlex.quote(str(d.get('phase', ''))))
print(f'imp_progress={int(d.get(\"progress\",0) or 0)}')
" 2>/dev/null)
	fi

	local imp_alive=false imp_elapsed=""
	if _pid_alive_as "$imp_pid" "eloop_improve"; then
		imp_alive=true
		imp_elapsed=$(_pid_elapsed "$imp_pid")
	fi
	local improve_ai_log="$TMP_DEBUG_DIR/improve_ai.log"
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
		imp_ai_output_block=$(tail -n "$ai_tail_lines" "$improve_ai_log" 2>/dev/null | awk '
/\[AI:[^]]+\] START/ { capture=1; block=""; next }
capture && /\[AI:[^]]+\] END/ { capture=0; next }
capture { block = block $0 ORS }
END { printf "%s", block }
')
		imp_ai_source=$(printf '%s' "$imp_ai_source" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x1f]//g')
		imp_ai_output_block=$(printf '%s' "$imp_ai_output_block" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/\r//g; s/[\x00-\x08\x0B-\x1F\x7F]//g')
		imp_ai_output_block=$(printf '%s\n' "$imp_ai_output_block" | sed '/^[[:space:]]*$/d' | tail -n "$ai_max_lines")
		if [[ -z "$imp_ai_output_block" ]]; then
			# START/END が取れない場合でも、直近の改善ログを最低限見せる
			imp_ai_output_block=$(tail -n "$ai_tail_lines" "$improve_ai_log" 2>/dev/null \
				| perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/\r//g; s/[\x00-\x08\x0B-\x1F\x7F]//g' \
				| grep -v '^\s*$' \
				| grep -v '\[IMPROVE\] job start' \
				| grep -v '\[IMPROVE\] attached pid=' \
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
else:
    print('acc_russia_count=0')
    print('acc_soviet=false')
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
	local stagnation_count=0 stagnation_event="none" stagnation_age="n/a" wildcard_origin_count=0
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
print("stagnation_event=" + shlex.quote(event))
print("stagnation_age=" + shlex.quote(age))
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
print(len(data) if isinstance(data, dict) else 0)
PY
)
	fi
	case "$wildcard_origin_count" in
	''|*[!0-9]*) wildcard_origin_count=0 ;;
	esac

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
    print("COUNT|0")
    raise SystemExit(0)

rows = []
for line in log_text.splitlines():
    parts = line.split("|", 2)
    if len(parts) == 3:
        rows.append(parts)

print(f"COUNT|{len(rows)}")
now = dt.datetime.now().astimezone()
try:
    last_dt = dt.datetime.fromisoformat(rows[0][1])
    print(f"LAST_AT|{last_dt.strftime('%Y-%m-%d %H:%M')}")
    print(f"LAST_AGE|{age_text((now - last_dt).total_seconds())}")
except Exception:
    pass

for commit, ad, subj in rows[:event_limit]:
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
		''|*[!0-9]*) say_lock_hb=$(stat -f %m tmp/.say_queue/.lock 2>/dev/null || echo 0) ;;
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

	# radio_state の "playing" は予約済み/待機中も含むため、実再生状況で補正
	local radio_effective_status="$radio_status"
	if [[ "$radio_effective_status" == "playing" ]]; then
		if ! $say_running; then
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

	# --- ラジオ deferred キュー状態 ---
	local radio_deferred_pending=0 radio_deferred_playing=0
	if [[ -d tmp/.radio_deferred_queue ]]; then
		radio_deferred_pending=$(find tmp/.radio_deferred_queue -name 'radio_*.txt' 2>/dev/null | wc -l | tr -d ' ')
		radio_deferred_playing=$(find tmp/.radio_deferred_queue -name 'radio_*.playing' 2>/dev/null | wc -l | tr -d ' ')
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

	# --- Outbound chat queue ---
	local outbound_pending=0
	if [[ -d tmp/.outbound_chat_queue/pending ]]; then
		outbound_pending=$(find tmp/.outbound_chat_queue/pending -name '*.msg' 2>/dev/null | wc -l | tr -d ' ')
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

	# 最新コメント
	local twitch_latest=""
	if [[ -f tmp/twitch_comments.txt ]] && [[ -s tmp/twitch_comments.txt ]]; then
		twitch_latest=$(tail -1 tmp/twitch_comments.txt)
		# "    ▸ Latest     " = 18 → text max = W-18
		local max_tw=$(( W - 18 ))
		(( ${#twitch_latest} > max_tw )) && twitch_latest="${twitch_latest[1,$((max_tw-3))]}..."
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
		printf "    ${C_GREEN}●${C_RESET} Loop        ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=${loop_pid}${C_RESET}\n"
	else
		printf "    ${C_RED}○${C_RESET} Loop        ${C_DIM}STOPPED${C_RESET}\n"
	fi

	# Worker 個別状態
	local _worker_rows=(
		"ChatW" "$chat_worker_running" "$chat_worker_pid"
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
		if [[ "$_w_running" == "true" ]]; then
			printf "    ${C_GREEN}●${C_RESET} %-11s ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=%s${C_RESET}\n" "$_w_name" "$_w_pid"
		elif [[ "$_w_name" == "PredW" && "$prediction_worker_paused" == "true" ]]; then
			printf "    ${C_YELLOW}◌${C_RESET} %-11s ${C_YELLOW}PAUSED${C_RESET}  ${C_DIM}tmp/state/prediction_worker.paused${C_RESET}\n" "$_w_name"
		else
			printf "    ${C_RED}○${C_RESET} %-11s ${C_DIM}STOPPED${C_RESET}\n" "$_w_name"
		fi
	done

	# ワーカー稼働メーター
	local workers_online=0 workers_total=6 workers_expected=6
	$prediction_worker_paused && workers_expected=5
	$loop_running && workers_online=$((workers_online + 1))
	$chat_worker_running && workers_online=$((workers_online + 1))
	$audio_worker_running && workers_online=$((workers_online + 1))
	$radio_worker_running && workers_online=$((workers_online + 1))
	$prediction_worker_running && workers_online=$((workers_online + 1))
	$improve_daemon_running && workers_online=$((workers_online + 1))
	local workers_bar
	workers_bar=$(_bar_meter "$workers_online" "$workers_expected" 12)
	printf "    ${C_WHITE}▸${C_RESET} Workers     ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d/%d expected online${C_RESET}\n" "$workers_bar" "$workers_online" "$workers_expected"
	if ! $improve_daemon_running && (( acc_count >= min_games )); then
		printf "    ${C_RED}!${C_RESET} ImproveD    ${C_RED}RESTART REQUIRED${C_RESET}  ${C_DIM}lock/improve gate reached${C_RESET}\n"
	elif ! $improve_daemon_running && (( min_games - acc_count <= 2 )); then
		printf "    ${C_YELLOW}!${C_RESET} ImproveD    ${C_YELLOW}restart soon${C_RESET}  ${C_DIM}%d games to improve gate${C_RESET}\n" "$(( min_games - acc_count ))"
	fi

		# 蓄積ゲーム (最低試合ゲート付き)
		if (( acc_count > 0 )); then
			local gate_color="$C_MAGENTA"
		(( acc_count >= min_games )) && gate_color="$C_GREEN"
		local count_label="${acc_count}/${min_games} games"
		local nation_label="R${acc_russia_count:-0}"
		$acc_soviet && nation_label="${nation_label} S=1"
		local max_scores=$(( W - 26 - ${#count_label} - ${#nation_label} ))
		(( max_scores < 8 )) && max_scores=8
		local scores_display="${acc_scores}"
		scores_display=$(_truncate_display_width_keep_tail "$scores_display" "$max_scores")
		printf "    ${gate_color}◆${C_RESET} Queued      ${gate_color}%s${C_RESET}  ${C_DIM}%s [%s]${C_RESET}\n" "${count_label}" "$nation_label" "${scores_display}"
	fi

	# キュー負荷メーター（show_status_g にはない運用系指標）
	local queue_total=$(( acc_count + comment_queue_count + twitch_pending ))
	local queue_bar
	queue_bar=$(_bar_meter "$queue_total" 30 12)
	printf "    ${C_BLUE}▸${C_RESET} QueueMeter  ${C_DIM}[%s]${C_RESET}  ${C_DIM}A=%d C=%d T=%d${C_RESET}\n" \
		"$queue_bar" "$acc_count" "$comment_queue_count" "$twitch_pending"

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
	fi
	printf "    ${C_MAGENTA}◇${C_RESET} Escape      ${escape_color}D=%s T=%s W=%s${C_RESET}  ${C_DIM}stag=%s/%s %s %s ago wc=%s${C_RESET}\n" \
		"$d_flag" "$t_flag" "$w_flag" "$stagnation_count" "$WILDCARD_TRIGGER_STAGNATION" "$stagnation_event" "$stagnation_age" "$wildcard_origin_count"

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

	if [[ -n "$twitch_latest" ]]; then
		printf "    ${C_DIM}▸ Latest     ${twitch_latest}${C_RESET}\n"
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
