#!/bin/zsh
# show_status.sh - eloop 全体のステータス表示
#
# Usage: ./show_status.sh       # 10秒間隔で常時表示
#        ./show_status.sh 3    # 3秒間隔で常時表示

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

WATCH_INTERVAL=${1:-10}
RANDOM_REFRESH_SEC=${RANDOM_REFRESH_SEC:-30}
RANDOM_CACHE_FILE="tmp/.status_random_cache"

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

_sanitize_line() {
	local line="$1"
	line=$(printf '%s' "$line" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g')
	line=$(printf '%s' "$line" | tr '\r\n' '  ' | tr -d '\000-\010\013\014\016-\037')
	line=$(printf '%s' "$line" | sed -E 's/[[:space:]]+/ /g; s/^ +//; s/ +$//')
	printf '%s' "$line"
}

_collect_random_snippets() {
	local -a snippets
	snippets=()

	[[ -f best_score.txt ]] && snippets+=("BEST SCORE: $(cat best_score.txt) pts")
	if [[ -f score_history.txt ]] && [[ -s score_history.txt ]]; then
		local total avg last
		total=$(wc -l < score_history.txt | tr -d ' ')
		avg=$(awk '{s+=$1}END{if(NR>0) printf "%.0f", s/NR; else print 0}' score_history.txt)
		last=$(tail -1 score_history.txt 2>/dev/null)
		snippets+=("${total} games played / avg ${avg} pts")
		[[ -n "$last" ]] && snippets+=("Last score: ${last} pts")
	fi
	[[ -f game_count.txt ]] && snippets+=("Game #$(cat game_count.txt) and counting...")

	if [[ -f game_state.json ]]; then
		local info
		info=$(python3 -c "import json; d=json.load(open('game_state.json')); print(f\"{len(d.get('pieces',[]))} pieces / {d.get('score',0)} pts / {d.get('state','?')}\")" 2>/dev/null)
		[[ -n "$info" ]] && snippets+=("Board: $info")
	fi

	if [[ -f tmp/past_radio_topics.txt ]] && [[ -s tmp/past_radio_topics.txt ]]; then
		local -a lines
		lines=("${(@f)$(cat tmp/past_radio_topics.txt)}")
		(( ${#lines} > 0 )) && snippets+=("${lines[$((RANDOM % ${#lines} + 1))]}")
	fi

	local latest_ver
	latest_ver=$(ls -1t strategy_versions/v[0-9]*_strategy.py 2>/dev/null | head -1)
	[[ -n "$latest_ver" ]] && snippets+=("Strategy: $(basename "$latest_ver")")

	if [[ -f tmp/improve_state.json ]]; then
		local imp
		imp=$(python3 -c "import json; print(json.load(open('tmp/improve_state.json')).get('status','?'))" 2>/dev/null)
		[[ -n "$imp" ]] && snippets+=("Improve: ${imp}")
	fi

	if [[ -f tmp/twitch_comments.txt ]] && [[ -s tmp/twitch_comments.txt ]]; then
		local -a clines
		clines=("${(@f)$(cat tmp/twitch_comments.txt)}")
		(( ${#clines} > 0 )) && snippets+=("Chat: ${clines[$((RANDOM % ${#clines} + 1))]}")
	fi

	if [[ -f tmp/news.txt ]] && [[ -s tmp/news.txt ]]; then
		local -a nlines
		nlines=("${(@f)$(grep -v '^$' tmp/news.txt)}")
		(( ${#nlines} > 0 )) && snippets+=("${nlines[$((RANDOM % ${#nlines} + 1))]}")
	fi

	(( ${#snippets} == 0 )) && return 1
	printf '%s\n' "${snippets[@]}"
}

_random_from_data() {
	local -a snippets selected used
	snippets=("${(@f)$(_collect_random_snippets 2>/dev/null)}")
	(( ${#snippets} == 0 )) && return 1
	local pick_count=$(( RANDOM % 2 + 1 ))
	(( pick_count > ${#snippets} )) && pick_count=${#snippets}
	selected=()
	used=()
	local _p=0
	while (( _p < pick_count )); do
		local idx=$(( RANDOM % ${#snippets} + 1 ))
		if (( ! ${used[(Ie)$idx]} )); then
			used+=($idx)
			selected+=("${snippets[$idx]}")
			(( _p++ ))
		fi
	done
	printf '%s' "${(pj: / :)selected}"
}

_random_from_fortune() {
	(( $+commands[fortune] )) || return 1
	local f
	f=$(fortune -s 2>/dev/null | head -n 1)
	f=$(_sanitize_line "$f")
	[[ -n "$f" ]] || return 1
	printf '%s' "$f"
}

_random_from_joke_loop() {
	local -a jokes words
	jokes=()
	(( $+commands[fortune] && $+commands[cowsay] )) && jokes+=("fortune | cowsay")
	(( $+commands[fortune] && $+commands[lolcat] )) && jokes+=("fortune | lolcat")
	(( $+commands[fortune] && $+commands[boxes] )) && jokes+=("fortune | boxes")
	(( $+commands[fortune] )) && jokes+=("fortune")
	(( $+commands[figlet] )) && jokes+=("figlet")
	(( $+commands[toilet] )) && jokes+=("toilet")
	(( $+commands[genact] )) && jokes+=("genact")
	(( ${#jokes} == 0 )) && return 1

	words=("HELLO" "LOL" "WOW" "NICE" "SOREN" "BOOM" "YAY" "COOL" "YEAH")
	local pick="${jokes[$((RANDOM % ${#jokes} + 1))]}"
	local word="${words[$((RANDOM % ${#words} + 1))]}"
	local out=""
	case "$pick" in
	figlet)
		out=$(echo "$word" | timeout 2 figlet 2>/dev/null | head -n 1)
		;;
	toilet)
		out=$(echo "$word" | timeout 2 toilet --gay 2>/dev/null | head -n 1)
		;;
	genact)
		out="joke_loop: genact"
		;;
	*)
		out=$(timeout 2 bash -c "$pick" 2>/dev/null | head -n 1)
		;;
	esac
	out=$(_sanitize_line "$out")
	[[ -z "$out" ]] && out="joke_loop: ${pick} ${word}"
	printf '%s' "$out"
}

_has_joke_loop_actions() {
	(( $+commands[fortune] && $+commands[cowsay] )) && return 0
	(( $+commands[fortune] && $+commands[lolcat] )) && return 0
	(( $+commands[fortune] && $+commands[boxes] )) && return 0
	(( $+commands[fortune] )) && return 0
	(( $+commands[figlet] )) && return 0
	(( $+commands[toilet] )) && return 0
	(( $+commands[genact] )) && return 0
	return 1
}

_random_from_fullscreen() {
	local -a cmds
	cmds=()
	(( $+commands[nyancat] )) && cmds+=("nyancat")
	(( $+commands[cmatrix] )) && cmds+=("cmatrix")
	(( $+commands[tty-clock] )) && cmds+=("tty-clock")
	(( $+commands[genact] )) && cmds+=("genact")
	(( ${#cmds} == 0 )) && return 1
	printf 'fullscreen: %s' "${cmds[$((RANDOM % ${#cmds} + 1))]}"
}

_has_fullscreen_actions() {
	(( $+commands[nyancat] )) && return 0
	(( $+commands[cmatrix] )) && return 0
	(( $+commands[tty-clock] )) && return 0
	(( $+commands[genact] )) && return 0
	return 1
}

_generate_random_status_line() {
	local -a actions
	actions=("data")
	(( $+commands[fortune] )) && actions+=("fortune")
	_has_joke_loop_actions && actions+=("joke")
	_has_fullscreen_actions && actions+=("fullscreen")

	local action="${actions[$((RANDOM % ${#actions} + 1))]}"
	local line=""
	case "$action" in
	data)
		line=$(_random_from_data 2>/dev/null)
		;;
	fortune)
		line=$(_random_from_fortune 2>/dev/null)
		;;
	joke)
		line=$(_random_from_joke_loop 2>/dev/null)
		;;
	fullscreen)
		line=$(_random_from_fullscreen 2>/dev/null)
		;;
	esac
	line=$(_sanitize_line "$line")
	[[ -z "$line" ]] && line="status stable / random panel idle"
	printf '%s' "$line"
}

_read_cached_random_line() {
	local now ts line
	now=$(date +%s)
	if [[ -f "$RANDOM_CACHE_FILE" ]]; then
		ts=$(head -n 1 "$RANDOM_CACHE_FILE" 2>/dev/null)
		line=$(tail -n +2 "$RANDOM_CACHE_FILE" 2>/dev/null | head -n 1)
		case "$ts" in
		''|*[!0-9]*) ;;
		*)
			if (( now - ts < RANDOM_REFRESH_SEC )) && [[ -n "$line" ]]; then
				printf '%s' "$line"
				return 0
			fi
			;;
		esac
	fi
	line=$(_generate_random_status_line)
	{
		echo "$now"
		echo "$line"
	} >"$RANDOM_CACHE_FILE"
	printf '%s' "$line"
}

#=== メイン表示 ===
show_status() {
	# --- 改善プロセス状態 ---
	local imp_status="idle" imp_pid=0 imp_hash="" imp_phase="" imp_progress=0
	if [[ -f tmp/improve_state.json ]]; then
		eval $(python3 -c "
import json
d=json.load(open('tmp/improve_state.json'))
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
	local improve_ai_log="tmp/improve_ai.log"
	local imp_ai_source="" imp_ai_latest="" imp_ai_age=""
	if [[ -f "$improve_ai_log" ]] && [[ -s "$improve_ai_log" ]]; then
		imp_ai_source=$(tail -n 200 "$improve_ai_log" 2>/dev/null | grep '\[AI:.*\] START' | tail -1 | sed -E 's/^\[[0-9:]+\] \[AI:[^]]+\] START //')
		imp_ai_latest=$(tail -n 200 "$improve_ai_log" 2>/dev/null | grep -v '\[AI:.*\] START' | grep -v '\[AI:.*\] END' | grep -v '^[[:space:]]*$' | tail -1)
		imp_ai_source=$(printf '%s' "$imp_ai_source" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x1f]//g')
		imp_ai_latest=$(printf '%s' "$imp_ai_latest" | perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x1f]//g')
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
	if [[ -f tmp/accumulated_games.json ]]; then
		acc_count=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('count',0))" 2>/dev/null)
		acc_scores=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('scores',''))" 2>/dev/null)
	fi

	# --- ローリングスコア & リグレッション ---
	local rolling_hash="" rolling_count=0 rolling_avg="" rolling_prev_avg=""
	local rejected_count=0
	if [[ -f tmp/rolling_scores.json ]] && [[ -f strategy.py ]]; then
		eval $(python3 -c "
import json, os, subprocess
rs = json.load(open('tmp/rolling_scores.json'))
h = subprocess.run(['python3', 'extract_decide_hash.py', 'strategy.py'],
    capture_output=True, text=True).stdout.strip()
if h and h in rs:
    scores = rs[h]['scores']
    prev_h = rs[h].get('prev_hash', '')
    avg = sum(scores)/len(scores) if scores else 0
    print(f'rolling_hash={h[:8]}')
    print(f'rolling_count={len(scores)}')
    print(f'rolling_avg={avg:.0f}')
    if prev_h and prev_h in rs and rs[prev_h]['scores']:
        prev_scores = rs[prev_h]['scores']
        prev_avg = sum(prev_scores)/len(prev_scores)
        print(f'rolling_prev_avg={prev_avg:.0f}')
" 2>/dev/null)
	fi
	[[ -f tmp/rejected_hashes.txt ]] && rejected_count=$(wc -l < tmp/rejected_hashes.txt | tr -d ' ')

	# --- リバートバックアップ ---
	local revert_available=false
	[[ -f tmp/revert_strategy.py ]] && revert_available=true

	# --- 最低試合ゲート ---
	local min_games=10

	# --- スコア情報 ---
	local best_score=$(cat best_score.txt 2>/dev/null || echo "?")
	local game_count=$(cat game_count.txt 2>/dev/null || echo "?")
	local last_scores=""
	[[ -f score_history.txt ]] && last_scores=$(tail -5 score_history.txt 2>/dev/null | tr '\n' ' ')

	# --- 戦略情報 ---
	local strategy_hash=$(md5 -q strategy.py 2>/dev/null | cut -c1-8)
	local strategy_ver=$(ls -1t strategy_versions/v[0-9]*_strategy.py 2>/dev/null | head -1 | xargs basename 2>/dev/null)
	local strategy_lines=$(wc -l < strategy.py 2>/dev/null | tr -d ' ')

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

	# --- ラジオコーナー状態 (状態ファイルベース) ---
	local radio_status="idle" radio_corner="" radio_elapsed=""
	if [[ -f tmp/.radio_state ]]; then
		local radio_line=$(cat tmp/.radio_state 2>/dev/null)
		local radio_mode=${radio_line%%:*}
		local rest=${radio_line#*:}
		radio_corner=${rest%%:*}
		local radio_ts=${rest##*:}
		if [[ -n "$radio_ts" ]]; then
			local age=$(( $(date +%s) - radio_ts ))
			if (( age > 600 )); then
				# 10分以上前 → stale
				radio_status="idle"
			else
				radio_status="$radio_mode"
				if (( age < 60 )); then radio_elapsed="${age}s"
				else radio_elapsed="$(( age / 60 ))m$(( age % 60 ))s"
				fi
			fi
		fi
	fi
	# 注: sayフォールバックは廃止 (コメント再生との区別不可のため状態ファイルのみで判定)
	# コーナー名が取れなかった場合、過去トピックスから取得
	if [[ -z "$radio_corner" ]] && [[ -f tmp/past_radio_topics.txt ]] && [[ -s tmp/past_radio_topics.txt ]]; then
		local last_radio_line=$(tail -1 tmp/past_radio_topics.txt)
		radio_corner=$(echo "$last_radio_line" | grep -oE '\[[a-z_]+\]' | tail -1 | tr -d '[]')
	fi

	# --- コメントキュー状態 ---
	local comment_queue_count=0
	if [[ -d tmp/.comment_queue ]]; then
		comment_queue_count=$(find tmp/.comment_queue -name 'comment_*.txt' 2>/dev/null | wc -l | tr -d ' ')
	fi

	# コメント生成プロセス (PIDファイル + 状態ファイル)
	local comment_gen_running=false comment_gen_pid=""
	if [[ -f tmp/.twitch_chat/comment_gen.pid ]]; then
		comment_gen_pid=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null)
		if [[ -n "$comment_gen_pid" ]] && kill -0 "$comment_gen_pid" 2>/dev/null; then
			comment_gen_running=true
		fi
	fi
	if ! $comment_gen_running && [[ -f tmp/.comment_gen_state ]]; then
		local cg_line=$(cat tmp/.comment_gen_state 2>/dev/null)
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

	# 改善プロセス
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
			(( ${#src_display} > max_src )) && src_display="${src_display[1,$((max_src-2))]}.."
			printf "    ${C_WHITE}▸${C_RESET} AIEngine    ${C_DIM}%s${C_RESET}\n" "$src_display"
		fi
		if [[ -n "$imp_ai_latest" ]]; then
			local ai_display="$imp_ai_latest"
			local max_ai=$(( W - 24 ))
			(( ${#ai_display} > max_ai )) && ai_display="${ai_display[1,$((max_ai-2))]}.."
			printf "    ${C_WHITE}▸${C_RESET} AIOutput    ${C_DIM}%s${C_RESET}" "$ai_display"
			[[ -n "$imp_ai_age" ]] && printf "  ${C_DIM}(%s)${C_RESET}" "$imp_ai_age"
			echo ""
		fi
	elif [[ "$imp_status" == "running" ]] && ! $imp_alive; then
		printf "    ${C_RED}✗${C_RESET} Improve     ${C_RED}STALE${C_RESET}  ${C_DIM}(PID=${imp_pid} dead, %d%% %s)${C_RESET}\n" "${imp_progress:-0}" "${imp_phase_label}"
		if [[ -n "$imp_ai_source" ]]; then
			local src_display="$imp_ai_source"
			local max_src=$(( W - 20 ))
			(( ${#src_display} > max_src )) && src_display="${src_display[1,$((max_src-2))]}.."
			printf "    ${C_WHITE}▸${C_RESET} AIEngine    ${C_DIM}%s${C_RESET}\n" "$src_display"
		fi
	else
		printf "    ${C_DIM}○${C_RESET} Improve     ${C_DIM}IDLE${C_RESET}\n"
	fi

	# 蓄積ゲーム (最低試合ゲート付き)
	if (( acc_count > 0 )); then
		local gate_color="$C_MAGENTA"
		(( acc_count >= min_games )) && gate_color="$C_GREEN"
		local count_label="${acc_count}/${min_games} games"
		local max_scores=$(( W - 22 - ${#count_label} ))
		local scores_display="${acc_scores}"
		(( ${#scores_display} > max_scores )) && scores_display="${scores_display[1,$((max_scores-2))]}.."
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
		echo ""
	else
		printf "    ${C_DIM}♪${C_RESET} Say         ${C_DIM}SILENT${C_RESET}"
		$say_locked && printf "  ${C_YELLOW}[locked]${C_RESET}"
		echo ""
	fi

	# ラジオコーナー
	local corner_label="${radio_corner:-?}"
	local elapsed_label=""
	[[ -n "$radio_elapsed" ]] && elapsed_label=" ${radio_elapsed}"
	case "$radio_status" in
		playing)
			printf "    ${C_GREEN}📻${C_RESET} Radio       ${C_GREEN}PLAYING${C_RESET}  ${C_DIM}[${corner_label}]${elapsed_label}${C_RESET}\n"
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
	# "    ▸ Version     " = 18, "  XXXL" = 6 → version name max = W-24
	local ver_display="${strategy_ver:-strategy.py}"
	local max_ver=$(( W - 24 ))
	(( ${#ver_display} > max_ver )) && ver_display="${ver_display[1,$((max_ver-2))]}.."
	printf "    ${C_WHITE}▸${C_RESET} Version     ${C_DIM}%s${C_RESET}  ${C_DIM}${strategy_lines}L${C_RESET}\n" "${ver_display}"
	# show_status_g と重複しない運用グラフ
	local ops_health=$(( workers_online * 20 ))
	(( ops_health < 0 )) && ops_health=0
	(( ops_health > 100 )) && ops_health=100
	local ops_bar
	ops_bar=$(_bar_meter "$ops_health" 100 12)
	printf "    ${C_WHITE}▸${C_RESET} OpsHealth   ${C_DIM}[%s]${C_RESET}  ${C_DIM}%d/100${C_RESET}\n" "$ops_bar" "$ops_health"

	local reject_bar
	reject_bar=$(_bar_meter "$rejected_count" 20 12)
	local revert_label="none"
	$revert_available && revert_label="ready"
	printf "    ${C_WHITE}▸${C_RESET} Rollback    ${C_DIM}[%s]${C_RESET}  ${C_DIM}rej=%d revert=%s${C_RESET}\n" \
		"$reject_bar" "$rejected_count" "$revert_label"

	# バッチサマリ
	if [[ -f tmp/batch_summary.txt ]] && [[ -s tmp/batch_summary.txt ]]; then
		local summary_age=$(_file_age tmp/batch_summary.txt)
		local summary_line=$(grep -v '^$' tmp/batch_summary.txt | grep -v '^===' | head -1)
		# "    ▸ Summary     " = 18, "  (XXX)" ~= 9 → text max = W-27
		local max_summ=$(( W - 27 ))
		(( ${#summary_line} > max_summ )) && summary_line="${summary_line[1,$((max_summ-2))]}.."
		printf "    ${C_WHITE}▸${C_RESET} Summary     ${C_DIM}%s  (%s)${C_RESET}\n" "${summary_line}" "${summary_age}"
	fi

	echo ""
	printf "  ${C_BOLD}RANDOM${C_RESET}\n"
	local random_line
	random_line=$(_read_cached_random_line)
	local max_random=$(( W - 18 ))
	(( ${#random_line} > max_random )) && random_line="${random_line[1,$((max_random-2))]}.."
	printf "    ${C_BLUE}▸${C_RESET} Pick        ${C_DIM}%s${C_RESET}\n" "$random_line"

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
	sleep "$WATCH_INTERVAL"
done
