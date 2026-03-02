#!/bin/zsh
# show_status.sh - eloop 改善プロセスのステータス表示
#
# Usage: ./show_status.sh          # 1回表示
#        ./show_status.sh watch    # 継続監視 (5秒間隔)
#        ./show_status.sh watch 3  # 継続監視 (3秒間隔)

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

WATCH_MODE=false
WATCH_INTERVAL=5
if [[ "$1" == "watch" ]]; then
	WATCH_MODE=true
	WATCH_INTERVAL=${2:-5}
fi

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

#=== メイン表示 ===
show_status() {
	local now=$(date '+%Y-%m-%d %H:%M:%S')

	# --- 改善プロセス状態 ---
	local imp_status="idle" imp_pid=0 imp_hash=""
	if [[ -f tmp/improve_state.json ]]; then
		imp_status=$(python3 -c "import json; print(json.load(open('tmp/improve_state.json')).get('status','idle'))" 2>/dev/null)
		imp_pid=$(python3 -c "import json; print(json.load(open('tmp/improve_state.json')).get('pid',0))" 2>/dev/null)
		imp_hash=$(python3 -c "import json; print(json.load(open('tmp/improve_state.json')).get('strategy_hash_before',''))" 2>/dev/null)
	fi

	# プロセス生存確認
	local pid_alive=false pid_cmd="" pid_elapsed=""
	if [[ "$imp_pid" -ne 0 ]] && kill -0 "$imp_pid" 2>/dev/null; then
		pid_cmd=$(ps -p "$imp_pid" -o command= 2>/dev/null | head -1)
		if echo "$pid_cmd" | grep -q "eloop_improve"; then
			pid_alive=true
			# 経過時間
			local pid_start=$(ps -p "$imp_pid" -o lstart= 2>/dev/null)
			if [[ -n "$pid_start" ]]; then
				local start_epoch=$(date -j -f "%a %b %d %T %Y" "$pid_start" "+%s" 2>/dev/null)
				local now_epoch=$(date "+%s")
				if [[ -n "$start_epoch" ]]; then
					local elapsed=$(( now_epoch - start_epoch ))
					local mins=$(( elapsed / 60 ))
					local secs=$(( elapsed % 60 ))
					pid_elapsed="${mins}m${secs}s"
				fi
			fi
		fi
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
		game_state=$(python3 -c "import json; d=json.load(open('game_state.json')); print(d.get('state','?'))" 2>/dev/null)
		game_score=$(python3 -c "import json; d=json.load(open('game_state.json')); print(d.get('score',0))" 2>/dev/null)
		game_pieces=$(python3 -c "import json; d=json.load(open('game_state.json')); print(len(d.get('pieces',[])))" 2>/dev/null)
	fi

	# --- 蓄積ゲーム ---
	local acc_count=0 acc_scores=""
	if [[ -f tmp/accumulated_games.json ]]; then
		acc_count=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('count',0))" 2>/dev/null)
		acc_scores=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('scores',''))" 2>/dev/null)
	fi

	# --- スコア情報 ---
	local best_score=$(cat best_score.txt 2>/dev/null || echo "?")
	local game_count=$(cat game_count.txt 2>/dev/null || echo "?")
	local last_scores=""
	if [[ -f score_history.txt ]]; then
		last_scores=$(tail -5 score_history.txt 2>/dev/null | tr '\n' ' ')
	fi

	# --- 戦略情報 ---
	local strategy_hash=$(md5 -q strategy.py 2>/dev/null | cut -c1-8)
	local strategy_ver=$(ls -1t strategy_versions/v[0-9]*_strategy.py 2>/dev/null | head -1 | xargs basename 2>/dev/null)
	local strategy_lines=$(wc -l < strategy.py 2>/dev/null | tr -d ' ')

	# --- スコアグラフ ---
	local score_graph=""
	if [[ -f score_history.txt ]] && (( $(wc -l < score_history.txt | tr -d ' ') >= 5 )); then
		score_graph=$(tail -20 score_history.txt | python3 -c "
import sys
scores = [int(l.strip()) for l in sys.stdin if l.strip().isdigit()]
if scores:
    lo, hi = min(scores), max(scores)
    bars = '▁▂▃▄▅▆▇█'
    r = hi - lo if hi != lo else 1
    print(''.join(bars[min(int((s-lo)/r*7),7)] for s in scores))
" 2>/dev/null)
	fi

	# ========== 描画 ==========
	echo ""
	printf "${C_BOLD}${C_CYAN}━━━ SOREN STATUS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}  ${C_DIM}${now}${C_RESET}\n"

	# メインループ
	if $loop_running; then
		printf "  ${C_GREEN}●${C_RESET} Loop      ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=${loop_pid}${C_RESET}\n"
	else
		printf "  ${C_RED}○${C_RESET} Loop      ${C_DIM}STOPPED${C_RESET}\n"
	fi

	# ゲーム状態
	local state_color="$C_DIM"
	case "$game_state" in
		MOVE)     state_color="$C_GREEN" ;;
		GAMEOVER) state_color="$C_RED" ;;
		STOP)     state_color="$C_RED" ;;
	esac
	printf "  ${C_WHITE}▸${C_RESET} Game      ${state_color}${game_state}${C_RESET}  ${C_DIM}score=${game_score} pieces=${game_pieces}${C_RESET}\n"

	echo ""

	# 改善プロセス
	if [[ "$imp_status" == "running" ]] && $pid_alive; then
		printf "  ${C_YELLOW}⟳${C_RESET} Improve   ${C_YELLOW}RUNNING${C_RESET}  ${C_DIM}PID=${imp_pid}${C_RESET}"
		[[ -n "$pid_elapsed" ]] && printf "  ${C_DIM}elapsed=${pid_elapsed}${C_RESET}"
		echo ""
		[[ -n "$imp_hash" ]] && printf "              ${C_DIM}hash_before=${imp_hash}  hash_now=${strategy_hash}${C_RESET}\n"
	elif [[ "$imp_status" == "running" ]] && ! $pid_alive; then
		printf "  ${C_RED}✗${C_RESET} Improve   ${C_RED}STALE${C_RESET}  ${C_DIM}(PID=${imp_pid} dead, state not harvested)${C_RESET}\n"
	else
		printf "  ${C_DIM}○${C_RESET} Improve   ${C_DIM}IDLE${C_RESET}\n"
	fi

	# 蓄積ゲーム
	if (( acc_count > 0 )); then
		printf "  ${C_MAGENTA}◆${C_RESET} Queued    ${C_MAGENTA}${acc_count} games${C_RESET}  ${C_DIM}scores=[${acc_scores}]${C_RESET}\n"
	fi

	echo ""

	# 戦略
	printf "  ${C_WHITE}▸${C_RESET} Strategy  ${C_DIM}${strategy_ver:-strategy.py}${C_RESET}  ${C_DIM}${strategy_lines}lines  hash=${strategy_hash}${C_RESET}\n"

	# スコア
	printf "  ${C_WHITE}▸${C_RESET} Games     ${C_BOLD}#${game_count}${C_RESET}  ${C_DIM}best=${C_RESET}${C_BOLD}${best_score}${C_RESET}\n"
	if [[ -n "$last_scores" ]]; then
		printf "  ${C_WHITE}▸${C_RESET} Recent    ${C_DIM}${last_scores}${C_RESET}\n"
	fi
	if [[ -n "$score_graph" ]]; then
		printf "  ${C_WHITE}▸${C_RESET} Trend     ${score_graph}  ${C_DIM}(last 20)${C_RESET}\n"
	fi

	# バッチサマリ (最新)
	if [[ -f tmp/batch_summary.txt ]] && [[ -s tmp/batch_summary.txt ]]; then
		local summary_age=""
		local summary_mod=$(stat -f '%m' tmp/batch_summary.txt 2>/dev/null)
		if [[ -n "$summary_mod" ]]; then
			local age=$(( $(date +%s) - summary_mod ))
			if (( age < 60 )); then
				summary_age="${age}s ago"
			elif (( age < 3600 )); then
				summary_age="$(( age / 60 ))m ago"
			else
				summary_age="$(( age / 3600 ))h ago"
			fi
		fi
		local summary_line=$(grep -v '^$' tmp/batch_summary.txt | grep -v '^===' | head -1)
		printf "  ${C_WHITE}▸${C_RESET} Summary   ${C_DIM}${summary_line}  (${summary_age})${C_RESET}\n"
	fi

	printf "${C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"
	echo ""
}

#=== 実行 ===
if $WATCH_MODE; then
	while true; do
		printf '\033[2J\033[H'
		show_status
		sleep "$WATCH_INTERVAL"
	done
else
	show_status
fi
