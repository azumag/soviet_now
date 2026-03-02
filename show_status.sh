#!/bin/zsh
# show_status.sh - eloop 全体のステータス表示
#
# Usage: ./show_status.sh       # 10秒間隔で常時表示
#        ./show_status.sh 3    # 3秒間隔で常時表示

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

WATCH_INTERVAL=${1:-10}


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

#=== メイン表示 ===
show_status() {
	local now=$(date '+%Y-%m-%d %H:%M:%S')

	# --- 改善プロセス状態 ---
	local imp_status="idle" imp_pid=0 imp_hash=""
	if [[ -f tmp/improve_state.json ]]; then
		eval $(python3 -c "
import json
d=json.load(open('tmp/improve_state.json'))
print(f'imp_status={d.get(\"status\",\"idle\")}')
print(f'imp_pid={d.get(\"pid\",0)}')
print(f'imp_hash={d.get(\"strategy_hash_before\",\"\")}')
" 2>/dev/null)
	fi

	local imp_alive=false imp_elapsed=""
	if _pid_alive_as "$imp_pid" "eloop_improve"; then
		imp_alive=true
		imp_elapsed=$(_pid_elapsed "$imp_pid")
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
	if [[ -f tmp/accumulated_games.json ]]; then
		acc_count=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('count',0))" 2>/dev/null)
		acc_scores=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('scores',''))" 2>/dev/null)
	fi

	# --- スコア情報 ---
	local best_score=$(cat best_score.txt 2>/dev/null || echo "?")
	local game_count=$(cat game_count.txt 2>/dev/null || echo "?")
	local last_scores=""
	[[ -f score_history.txt ]] && last_scores=$(tail -5 score_history.txt 2>/dev/null | tr '\n' ' ')

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

	# --- ラジオトーク状態 ---
	local radio_status="idle"
	local radio_text_len=0
	if [[ -f tmp/radio_talk_playing ]]; then
		radio_status="playing"
	elif [[ -f tmp/radio_talk.txt ]] && [[ -s tmp/radio_talk.txt ]]; then
		radio_status="ready"
		radio_text_len=$(wc -c < tmp/radio_talk.txt | tr -d ' ')
	fi

	# ラジオ生成中かチェック (eloop_improve.sh 内で生成)
	if $imp_alive && [[ "$radio_status" == "idle" ]]; then
		# 改善プロセスが動いていてまだラジオが無い → 生成待ちかも
		radio_status="pending"
	fi

	# --- コメントキュー状態 ---
	local comment_queue_count=0
	if [[ -d tmp/.comment_queue ]]; then
		comment_queue_count=$(find tmp/.comment_queue -name 'comment_*.txt' 2>/dev/null | wc -l | tr -d ' ')
	fi

	# コメント生成プロセス
	local comment_gen_running=false comment_gen_pid=""
	if [[ -f tmp/.twitch_chat/comment_gen.pid ]]; then
		comment_gen_pid=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null)
		if [[ -n "$comment_gen_pid" ]] && kill -0 "$comment_gen_pid" 2>/dev/null; then
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
		(( ${#twitch_latest} > 60 )) && twitch_latest="${twitch_latest[1,57]}..."
	fi

	# ========== 描画 ==========
	echo ""
	printf "${C_BOLD}${C_CYAN}━━━ SOREN STATUS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}  ${C_DIM}${now}${C_RESET}\n"
	echo ""

	# === セクション: Core ===
	printf "  ${C_BOLD}CORE${C_RESET}\n"

	# メインループ
	if $loop_running; then
		printf "    ${C_GREEN}●${C_RESET} Loop        ${C_GREEN}RUNNING${C_RESET}  ${C_DIM}PID=${loop_pid}${C_RESET}\n"
	else
		printf "    ${C_RED}○${C_RESET} Loop        ${C_DIM}STOPPED${C_RESET}\n"
	fi

	# ゲーム状態
	local state_color="$C_DIM"
	case "$game_state" in
		MOVE)     state_color="$C_GREEN" ;;
		GAMEOVER) state_color="$C_RED" ;;
		STOP)     state_color="$C_RED" ;;
	esac
	printf "    ${C_WHITE}▸${C_RESET} Game        ${state_color}${game_state}${C_RESET}  ${C_DIM}score=${game_score} pieces=${game_pieces}${C_RESET}\n"

	# 改善プロセス
	if [[ "$imp_status" == "running" ]] && $imp_alive; then
		printf "    ${C_YELLOW}⟳${C_RESET} Improve     ${C_YELLOW}RUNNING${C_RESET}  ${C_DIM}PID=${imp_pid}${C_RESET}"
		[[ -n "$imp_elapsed" ]] && printf "  ${C_DIM}${imp_elapsed}${C_RESET}"
		echo ""
	elif [[ "$imp_status" == "running" ]] && ! $imp_alive; then
		printf "    ${C_RED}✗${C_RESET} Improve     ${C_RED}STALE${C_RESET}  ${C_DIM}(PID=${imp_pid} dead)${C_RESET}\n"
	else
		printf "    ${C_DIM}○${C_RESET} Improve     ${C_DIM}IDLE${C_RESET}\n"
	fi

	# 蓄積ゲーム
	if (( acc_count > 0 )); then
		printf "    ${C_MAGENTA}◆${C_RESET} Queued      ${C_MAGENTA}${acc_count} games${C_RESET}  ${C_DIM}[${acc_scores}]${C_RESET}\n"
	fi

	echo ""

	# === セクション: Audio ===
	printf "  ${C_BOLD}AUDIO${C_RESET}\n"

	# TTS (say)
	if $say_running; then
		printf "    ${C_GREEN}♪${C_RESET} Say         ${C_GREEN}PLAYING${C_RESET}  ${C_DIM}PID=${say_pid}${C_RESET}"
		$say_locked && printf "  ${C_DIM}[locked]${C_RESET}"
		echo ""
	else
		printf "    ${C_DIM}♪${C_RESET} Say         ${C_DIM}SILENT${C_RESET}"
		$say_locked && printf "  ${C_YELLOW}[locked]${C_RESET}"
		echo ""
	fi

	# ラジオ
	case "$radio_status" in
		playing)
			printf "    ${C_GREEN}📻${C_RESET} Radio       ${C_GREEN}PLAYING${C_RESET}\n"
			;;
		ready)
			printf "    ${C_YELLOW}📻${C_RESET} Radio       ${C_YELLOW}READY${C_RESET}  ${C_DIM}${radio_text_len}chars waiting${C_RESET}\n"
			;;
		pending)
			printf "    ${C_CYAN}📻${C_RESET} Radio       ${C_CYAN}GENERATING${C_RESET}\n"
			;;
		*)
			printf "    ${C_DIM}📻${C_RESET} Radio       ${C_DIM}IDLE${C_RESET}\n"
			;;
	esac

	# コメント読み上げキュー
	if (( comment_queue_count > 0 )); then
		printf "    ${C_MAGENTA}💬${C_RESET} CommentQ    ${C_MAGENTA}${comment_queue_count} pending${C_RESET}\n"
	else
		printf "    ${C_DIM}💬${C_RESET} CommentQ    ${C_DIM}empty${C_RESET}\n"
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
	printf "  ${C_BOLD}STRATEGY${C_RESET}\n"
	printf "    ${C_WHITE}▸${C_RESET} Version     ${C_DIM}${strategy_ver:-strategy.py}${C_RESET}  ${C_DIM}${strategy_lines}L${C_RESET}\n"
	printf "    ${C_WHITE}▸${C_RESET} Games       ${C_BOLD}#${game_count}${C_RESET}  ${C_DIM}best=${C_RESET}${C_BOLD}${best_score}${C_RESET}\n"
	[[ -n "$last_scores" ]] && \
		printf "    ${C_WHITE}▸${C_RESET} Recent      ${C_DIM}${last_scores}${C_RESET}\n"
	[[ -n "$score_graph" ]] && \
		printf "    ${C_WHITE}▸${C_RESET} Trend       ${score_graph}  ${C_DIM}(last 20)${C_RESET}\n"

	# バッチサマリ
	if [[ -f tmp/batch_summary.txt ]] && [[ -s tmp/batch_summary.txt ]]; then
		local summary_age=$(_file_age tmp/batch_summary.txt)
		local summary_line=$(grep -v '^$' tmp/batch_summary.txt | grep -v '^===' | head -1)
		printf "    ${C_WHITE}▸${C_RESET} Summary     ${C_DIM}${summary_line}  (${summary_age})${C_RESET}\n"
	fi

	echo ""
	printf "${C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"
	echo ""
}

#=== 実行 ===
printf '\033[?25l'          # カーソル非表示
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
printf '\033[2J'            # 初回だけ画面クリア
while true; do
	# 出力をバッファし、各行に行末クリア(\033[K)を付けて一括描画
	local buf
	buf=$(show_status)
	printf '\033[H'
	echo "$buf" | while IFS= read -r line; do
		printf '%s\033[K\n' "$line"
	done
	printf '\033[J'         # 残り行を消去
	sleep "$WATCH_INTERVAL"
done
