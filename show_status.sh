#!/bin/zsh
# show_status.sh - eloop 全体のステータス表示
#
# Usage: ./show_status.sh       # 10秒間隔で常時表示
#        ./show_status.sh 3    # 3秒間隔で常時表示

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

WATCH_INTERVAL=${1:-10}
FULLSCREEN_ENABLED=${FULLSCREEN_ENABLED:-1}
FULLSCREEN_RARE_N=${FULLSCREEN_RARE_N:-30}
FULLSCREEN_MIN_GAP_SEC=${FULLSCREEN_MIN_GAP_SEC:-180}
TMP_STATE_DIR="tmp/state"
TMP_MARKERS_DIR="tmp/markers"
TMP_HISTORY_DIR="tmp/history"
TMP_DEBUG_DIR="tmp/debug"
CURRENT_STRATEGY_RUN_FILE="$TMP_STATE_DIR/current_strategy_run.json"
FULLSCREEN_LAST_FILE="$TMP_STATE_DIR/.status_fullscreen_last"
MIN_GAMES_BEFORE_IMPROVE=${MIN_GAMES_BEFORE_IMPROVE:-12}
MIN_GAMES_BEFORE_REGRESSION=${MIN_GAMES_BEFORE_REGRESSION:-12}
MIN_GAMES_FOR_BEST_ROLLBACK=${MIN_GAMES_FOR_BEST_ROLLBACK:-12}
REGRESSION_MAX_RANK=${REGRESSION_MAX_RANK:-20}
REGRESSION_COMPOSITE_RATIO=${REGRESSION_COMPOSITE_RATIO:-0.88}
REGRESSION_P50_RATIO=${REGRESSION_P50_RATIO:-0.85}
REGRESSION_P25_RATIO=${REGRESSION_P25_RATIO:-0.80}
REGRESSION_MIN_COMP_GAP=${REGRESSION_MIN_COMP_GAP:-120}
REGRESSION_MIN_P50_GAP=${REGRESSION_MIN_P50_GAP:-100}
REGRESSION_MIN_P25_GAP=${REGRESSION_MIN_P25_GAP:-180}
RADIO_STATE_STALE_SEC=${RADIO_STATE_STALE_SEC:-600}

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

# PIDが生きていて指定パターンのプロセスかチェック
_pid_alive_as() {
	local pid="$1" pattern="$2"
	[[ "$pid" -ne 0 ]] 2>/dev/null || return 1
	kill -0 "$pid" 2>/dev/null || return 1
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

_fullscreen_commands() {
	local -a cmds
	cmds=()
	(( $+commands[nyancat] )) && cmds+=("nyancat")
	(( $+commands[genact] )) && cmds+=("genact")
	(( $+commands[cmatrix] )) && cmds+=("cmatrix")
	(( $+commands[tty-clock] )) && cmds+=("tty-clock")
	(( $+commands[sl] )) && cmds+=("sl")
	printf '%s\n' "${cmds[@]}"
}

_run_fullscreen_command() {
	local cmd="$1"
	case "$cmd" in
	nyancat)
		TERM=vt100 timeout 10 nyancat 2>/dev/null || true
		printf '\033[0m\n'
		;;
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
import json
d=json.load(open('$TMP_STATE_DIR/improve_state.json'))
print(f'imp_status={d.get(\"status\",\"idle\")}')
print(f'imp_pid={d.get(\"pid\",0)}')
print(f'imp_hash={d.get(\"strategy_hash_before\",\"\")}')
print(f'imp_phase={d.get(\"phase\",\"\")}')
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
	if [[ -f tmp/soren_loop.lock ]]; then
		loop_pid=$(cat tmp/soren_loop.lock 2>/dev/null)
		if [[ -n "$loop_pid" ]] && kill -0 "$loop_pid" 2>/dev/null; then
			loop_running=true
		fi
	fi

	# --- ゲーム状態 ---
	local game_state="" game_score=0 game_pieces=0
	if [[ -f game_state.json ]]; then
		eval $(python3 -c "
import json
d=json.load(open('game_state.json'))
print(f'game_state={d.get(\"state\",\"?\")}')
print(f'game_score={d.get(\"score\",0)}')
print(f'game_pieces={len(d.get(\"pieces\",[]))}')
" 2>/dev/null)
	fi

	# --- 蓄積ゲーム ---
	local acc_count=0 acc_scores=""
	if [[ -f $TMP_STATE_DIR/accumulated_games.json ]]; then
		local current_hash_for_acc=""
		current_hash_for_acc=$(python3 extract_decide_hash.py strategy.py 2>/dev/null || echo "")
		acc_count=$(python3 -c "import json; d=json.load(open('$TMP_STATE_DIR/accumulated_games.json')); h=d.get('hash',''); print(d.get('count',0) if (h and h == '$current_hash_for_acc') else 0)" 2>/dev/null)
		acc_scores=$(python3 -c "import json; d=json.load(open('$TMP_STATE_DIR/accumulated_games.json')); h=d.get('hash',''); print(d.get('scores','') if (h and h == '$current_hash_for_acc') else '')" 2>/dev/null)
	fi

	# --- ローリングスコア & リグレッション ---
	local rolling_hash="" rolling_count=0 rolling_avg="" rolling_prev_avg=""
	local rolling_comp="" rolling_p50="" rolling_p25="" rolling_total=""
	local best_hash_short="" best_comp="" best_p50="" best_p25="" best_total="" best_source_short=""
	local regression_state="" regression_detail=""
		local rejected_count=0
		if [[ -f $TMP_STATE_DIR/rolling_scores.json ]] && [[ -f strategy.py ]]; then
			eval "$(
				python3 - <<PY 2>/dev/null
import json
import math
import os
import shlex
import subprocess

rs = json.load(open("$TMP_STATE_DIR/rolling_scores.json"))
h = subprocess.run(
    ["python3", "extract_decide_hash.py", "strategy.py"],
    capture_output=True,
    text=True,
).stdout.strip()
current_run = {}
try:
    current_run = json.load(open("$CURRENT_STRATEGY_RUN_FILE"))
except Exception:
    current_run = {}
min_games_candidates = int(${MIN_GAMES_FOR_BEST_ROLLBACK})
min_games_current = int(${MIN_GAMES_BEFORE_REGRESSION})
max_rank = int(${REGRESSION_MAX_RANK})
archive_dir = "strategy_versions/by_hash"
keep_top = int(${HASH_ARCHIVE_KEEP_TOP:-50})
score_history_file = "score_history.txt"

def quantile(xs, q):
    ys = sorted(float(v) for v in xs)
    n = len(ys)
    if n == 0:
        return 0.0
    if n == 1:
        return ys[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    frac = pos - lo
    return ys[lo] * (1.0 - frac) + ys[hi] * frac

def metrics(scores):
    xs = [float(v) for v in scores]
    n = len(xs)
    if n <= 0:
        return None
    avg = sum(xs) / n
    p50 = quantile(xs, 0.50)
    p25 = quantile(xs, 0.25)
    if n > 1:
        var = sum((s - avg) ** 2 for s in xs) / n
        std = math.sqrt(max(var, 0.0))
    else:
        std = 0.0
    lcb = avg - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"avg": avg, "comp": comp, "p50": p50, "p25": p25, "n": n}

def mature_rank_rows(current_metrics):
    ranked = []
    for hh, data in rs.items():
        if hh == h:
            continue
        m2 = metrics(data.get("scores", []))
        if not m2 or m2["n"] < min_games_candidates:
            continue
        if archive_dir and not os.path.exists(os.path.join(archive_dir, f"{hh}.py")):
            continue
        ranked.append((m2["comp"], m2["p50"], m2["p25"], m2["n"], hh, m2, "ranking"))
    if current_metrics:
        ranked.append((current_metrics["comp"], current_metrics["p50"], current_metrics["p25"], current_metrics["n"], h, current_metrics, "current"))
    ranked.sort(reverse=True)
    return ranked

if h:
    current_data = {"hash": h, "scores": [], "games_total": 0}
    if str(current_run.get("hash", "") or "") == h:
        current_data = current_run
    scores = current_data.get("scores", [])
    m = metrics(scores)
    games_total = current_data.get("games_total", len(scores))
    try:
        games_total = int(games_total)
    except Exception:
        games_total = len(scores)
    avg = m["avg"] if m else 0
    print(f"rolling_hash={h[:8]}")
    print(f"rolling_count={games_total}")
    print(f"rolling_avg={avg:.0f}")
    if prev_h and prev_h in rs and rs[prev_h]["scores"]:
        prev_scores = rs[prev_h]["scores"]
        prev_avg = sum(prev_scores) / len(prev_scores)
        print(f"rolling_prev_avg={prev_avg:.0f}")
    print(f"rolling_total={games_total}")
    if m:
        comp = m["comp"]
        p50 = m["p50"]
        p25 = m["p25"]
        n = m["n"]
        print(f"rolling_comp={comp:.0f}")
        print(f"rolling_p50={p50:.0f}")
        print(f"rolling_p25={p25:.0f}")
        ranked = mature_rank_rows({"comp": comp, "p50": p50, "p25": p25, "n": n})
        if ranked:
            cutoff = ranked[min(max_rank, len(ranked)) - 1]
            cc, cp50, cp25, cn, ch, _, csource = cutoff
            print(f"best_hash_short={ch[:8]}")
            print(f"best_comp={cc:.0f}")
            print(f"best_p50={cp50:.0f}")
            print(f"best_p25={cp25:.0f}")
            print(f"best_total={cn}")
            print("best_source_short=" + shlex.quote("rank_cutoff"))
            if int(n) < min_games_current:
                detail = "WAIT rank=?/" + str(max_rank) + f" cutoff={ch[:8]} n={int(n)}/{min_games_current}"
                print("regression_state=grace")
                print("regression_detail=" + shlex.quote(detail))
                raise SystemExit
            current_rank = None
            for idx, row in enumerate(ranked, start=1):
                if row[4] == h:
                    current_rank = idx
                    break
            if current_rank is None or len(ranked) <= max_rank or current_rank <= max_rank:
                detail = "NO rank=" + str(current_rank or "?") + f"/{max_rank} cutoff={ch[:8]} n={int(n)}"
                print("regression_state=safe")
                print("regression_detail=" + shlex.quote(detail))
            else:
                detail = "YES rank=" + str(current_rank) + f"/{max_rank} cutoff={ch[:8]} n={int(n)}"
                print("regression_state=trigger")
                print("regression_detail=" + shlex.quote(detail))
        else:
            print("regression_state=safe")
            print("regression_detail=" + shlex.quote("NO no mature ranking"))
PY
			)"
		fi
	[[ -f $TMP_HISTORY_DIR/rejected_hashes.txt ]] && rejected_count=$(wc -l < $TMP_HISTORY_DIR/rejected_hashes.txt | tr -d ' ')

	# --- リバートバックアップ ---
	local revert_available=false
	[[ -f tmp/revert_strategy.py ]] && revert_available=true

	# --- 最低試合ゲート ---
	local min_games=12

	# --- スコア情報 ---
	local best_score=$(cat best_score.txt 2>/dev/null || echo "?")
	local game_count=$(cat game_count.txt 2>/dev/null || echo "?")
	local last_scores=""
	[[ -f score_history.txt ]] && last_scores=$(tail -5 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' | tr '\n' ' ')

	# --- 戦略情報 ---
	local strategy_ver=$(ls -1t strategy_versions/v[0-9]*_score[0-9]*_strategy.py 2>/dev/null | head -1 | xargs basename 2>/dev/null)
	local strategy_lines=$(wc -l < strategy.py 2>/dev/null | tr -d ' ')
	local strategy_decide_hash="?"
	strategy_decide_hash=$(python3 extract_decide_hash.py strategy.py 2>/dev/null || echo "?")

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
	done < <(python3 - <<'PY'
import ast
import datetime as dt
import hashlib
import re
import subprocess


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

for commit, ad, subj in rows[:2]:
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
		if [[ -n "$say_pid" ]] && kill -0 "$say_pid" 2>/dev/null; then
			say_running=true
		fi
	fi
	# pgrep でも確認 (pidファイルがなくても say が動いている場合)
	if ! $say_running; then
		say_pid=$(pgrep -x say 2>/dev/null | head -1)
		[[ -n "$say_pid" ]] && say_running=true
	fi

	# say_queue のロック状態
	local say_locked=false
	[[ -d tmp/.say_queue/.lock ]] && say_locked=true

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
		*"/tmp/eloop_radio_talk_"*|*"tmp/.radio_deferred_queue/radio_"*|*"tmp/radio_celebration.txt"*)
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
	local comment_queue_count=0
	if [[ -d tmp/.comment_queue ]]; then
		comment_queue_count=$(find tmp/.comment_queue -name 'comment_*.txt' 2>/dev/null | wc -l | tr -d ' ')
	fi
	local manual_audio_trigger_count=0
	if [[ -d tmp/.manual_audio_triggers ]]; then
		manual_audio_trigger_count=$(find tmp/.manual_audio_triggers -name '*.cmd' 2>/dev/null | wc -l | tr -d ' ')
	fi

	# コメント生成プロセス (PIDファイル + 状態ファイル)
	local comment_gen_running=false comment_gen_pid=""
	if [[ -f tmp/.twitch_chat/comment_gen.pid ]]; then
		comment_gen_pid=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null)
		comment_gen_pid=${comment_gen_pid%%|*}
		if [[ -n "$comment_gen_pid" ]] && kill -0 "$comment_gen_pid" 2>/dev/null; then
			comment_gen_running=true
		fi
	fi
	if ! $comment_gen_running && [[ -f $TMP_STATE_DIR/.comment_gen_state ]]; then
		local cg_line=$(cat $TMP_STATE_DIR/.comment_gen_state 2>/dev/null)
		local cg_ts=${cg_line##*:}
		if [[ -n "$cg_ts" ]] && (( $(date +%s) - cg_ts < 300 )); then
			comment_gen_running=true
		fi
	fi

	# --- Twitch チャット状態 ---
	local twitch_running=false twitch_pid=""
	if [[ -f tmp/.twitch_chat/daemon.pid ]]; then
		twitch_pid=$(cat tmp/.twitch_chat/daemon.pid 2>/dev/null)
		if [[ -n "$twitch_pid" ]] && kill -0 "$twitch_pid" 2>/dev/null; then
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

	# ワーカー稼働メーター（show_status_g にはない運用系指標）
	local workers_online=0
	$loop_running && workers_online=$((workers_online + 1))
	if [[ "$imp_status" == "running" ]] && $imp_alive; then
		workers_online=$((workers_online + 1))
	fi
	$say_running && workers_online=$((workers_online + 1))
	$twitch_running && workers_online=$((workers_online + 1))
	$comment_gen_running && workers_online=$((workers_online + 1))
	local workers_bar
	workers_bar=$(_bar_meter "$workers_online" 5 12)
	printf "    ${C_WHITE}▸${C_RESET} Workers     ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d/5 online${C_RESET}\n" "$workers_bar" "$workers_online"

		# 蓄積ゲーム (最低試合ゲート付き)
		if (( acc_count > 0 )); then
			local gate_color="$C_MAGENTA"
		(( acc_count >= min_games )) && gate_color="$C_GREEN"
		local count_label="${acc_count}/${min_games} games"
		local max_scores=$(( W - 22 - ${#count_label} ))
		local scores_display="${acc_scores}"
		scores_display=$(_truncate_display_width_keep_tail "$scores_display" "$max_scores")
		printf "    ${gate_color}◆${C_RESET} Queued      ${gate_color}%s${C_RESET}  ${C_DIM}[%s]${C_RESET}\n" "${count_label}" "${scores_display}"
	fi

	# キュー負荷メーター（show_status_g にはない運用系指標）
	local queue_total=$(( acc_count + comment_queue_count + twitch_pending ))
	local queue_bar
	queue_bar=$(_bar_meter "$queue_total" 30 12)
	printf "    ${C_BLUE}▸${C_RESET} QueueMeter  ${C_DIM}[%s]${C_RESET}  ${C_DIM}A=%d C=%d T=%d${C_RESET}\n" \
		"$queue_bar" "$acc_count" "$comment_queue_count" "$twitch_pending"

	# リバート・リジェクト情報
	if $revert_available || (( rejected_count > 0 )); then
		local revert_info=""
		$revert_available && revert_info="${C_DIM}revert=ready${C_RESET}"
		local reject_info=""
		(( rejected_count > 0 )) && reject_info="  ${C_DIM}rejected=${rejected_count}${C_RESET}"
		printf "    ${C_DIM}▸${C_RESET} Safety      ${revert_info}${reject_info}\n"
	fi

	echo ""

	# === セクション: Audio ===
	printf "  ${C_BOLD}AUDIO${C_RESET}\n"

	# TTS (say)
		if $say_running; then
			printf "    ${C_GREEN}♪${C_RESET} Say         ${C_GREEN}PLAYING${C_RESET}  ${C_DIM}PID=${say_pid}${C_RESET}"
			$say_locked && printf "  ${C_DIM}[locked]${C_RESET}"
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
		else
			printf "    ${C_DIM}♪${C_RESET} Say         ${C_DIM}SILENT${C_RESET}"
			$say_locked && printf "  ${C_YELLOW}[locked]${C_RESET}"
			if [[ -n "$say_source" ]]; then
				local last_label="${say_label:-${say_phase:-?}}"
				printf "  ${C_YELLOW}[last:%s:%s:%s]${C_RESET}" "$last_label" "${say_phase:-?}" "${say_source_age:-?}"
			fi
			echo ""
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

	# コメント読み上げキュー
	if (( comment_queue_count > 0 )); then
		printf "    ${C_MAGENTA}💬${C_RESET} CommentQ    ${C_MAGENTA}${comment_queue_count} pending${C_RESET}\n"
	else
		printf "    ${C_DIM}💬${C_RESET} CommentQ    ${C_DIM}empty${C_RESET}\n"
	fi
	if (( manual_audio_trigger_count > 0 )); then
		printf "    ${C_CYAN}⌘${C_RESET} TriggerQ    ${C_CYAN}${manual_audio_trigger_count} pending${C_RESET}\n"
	fi

	# コメント生成
	if $comment_gen_running; then
		printf "    ${C_YELLOW}⟳${C_RESET} CommentGen  ${C_YELLOW}RUNNING${C_RESET}  ${C_DIM}PID=${comment_gen_pid}${C_RESET}\n"
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

	if [[ -n "$twitch_latest" ]]; then
		printf "    ${C_DIM}▸ Latest     ${twitch_latest}${C_RESET}\n"
	fi

	echo ""

	# === セクション: Strategy & Scores ===
	printf "  ${C_BOLD}STRATEGY / SAFETY${C_RESET}\n"
	# "    ▸ Version     " = 18, "  XXXL" = 6 → version name max = W-24
	local ver_display="${strategy_ver:-strategy.py}"
	local max_ver=$(( W - 24 ))
	(( ${#ver_display} > max_ver )) && ver_display="${ver_display[1,$((max_ver-2))]}.."
	printf "    ${C_WHITE}▸${C_RESET} Version     ${C_DIM}%s${C_RESET}  ${C_DIM}${strategy_lines}L${C_RESET}\n" "${ver_display}"
	printf "    ${C_WHITE}▸${C_RESET} DecideHash  ${C_DIM}%s${C_RESET}\n" "${strategy_decide_hash}"

	# Score metrics + trend bar for current strategy
		if [[ -n "$rolling_comp" ]]; then
			printf "    ${C_WHITE}▸${C_RESET} Score       ${C_DIM}comp=%s p50=%s q25=%s  n=%s${C_RESET}\n" \
				"$rolling_comp" "$rolling_p50" "$rolling_p25" "${rolling_total:-0}"
			if [[ -n "$best_comp" ]]; then
				printf "    ${C_WHITE}▸${C_RESET} BestRef     ${C_DIM}%s(%s)  comp=%s p50=%s q25=%s  n=%s${C_RESET}\n" \
					"${best_hash_short:-?}" "${best_source_short:-rolling}" "$best_comp" "$best_p50" "$best_p25" "${best_total:-0}"
			fi
			if [[ -n "$regression_state" ]]; then
				local reg_color="$C_DIM"
				case "$regression_state" in
					trigger) reg_color="$C_RED" ;;
					safe) reg_color="$C_GREEN" ;;
				esac
				printf "    ${C_WHITE}▸${C_RESET} Regression  ${reg_color}%s${C_RESET}\n" "${regression_detail:-N/A}"
			fi
			# Trend mini-bar: comp normalized to max 2000, width 20
			local bar_max=2000 bar_width=20
		local bar_filled=$(( rolling_comp * bar_width / bar_max ))
		(( bar_filled > bar_width )) && bar_filled=$bar_width
		(( bar_filled < 0 )) && bar_filled=0
		local bar_empty=$(( bar_width - bar_filled ))
		local trend_suffix="avg ${rolling_avg:-?}"
		if [[ -n "$rolling_prev_avg" ]] && (( rolling_prev_avg > 0 )); then
			local diff_pct=$(( (rolling_avg - rolling_prev_avg) * 100 / rolling_prev_avg ))
			local sign=""; (( diff_pct >= 0 )) && sign="+"
			trend_suffix="${trend_suffix} vs prev ${rolling_prev_avg} ${sign}${diff_pct}%"
		fi
		printf "    ${C_WHITE}▸${C_RESET} Trend       ${C_GREEN}%s${C_DIM}%s${C_RESET} ${C_DIM}(%s)${C_RESET}\n" \
			"$(printf '%0.s█' $(seq 1 $((bar_filled > 0 ? bar_filled : 1))))" \
			"$(printf '%0.s░' $(seq 1 $((bar_empty > 0 ? bar_empty : 1))))" \
			"$trend_suffix"
	fi

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

render() {
	local buf=""
	while IFS= read -r line; do
		buf+="${line}${CLR}"$'\n'
	done
	printf '\033[H%s\033[J' "$buf"
}

#=== 実行 ===
printf '\033[?25l'          # カーソル非表示
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
printf '\033[2J'            # 初回だけ画面クリア
while true; do
	show_status | render
	if _maybe_run_fullscreen_random; then
		continue
	fi
	sleep "$WATCH_INTERVAL"
done
