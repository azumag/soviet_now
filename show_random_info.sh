#!/bin/zsh
# show_random_info.sh - eloopの出力からランダムにピックアップして面白く表示
#
# Usage: ./show_random_info.sh [回数(default:1)]
#        ./show_random_info.sh loop [間隔秒(default:5)]

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

# tmp/ subdirectory paths (mirroring eloop_lib.sh structure)
TMP_STATE_DIR="tmp/state"
TMP_HISTORY_DIR="tmp/history"
TMP_DEBUG_DIR="tmp/debug"

LOOP_MODE=false
LOOP_INTERVAL=5

if [[ "$1" == "loop" ]]; then
	LOOP_MODE=true
	LOOP_INTERVAL=${2:-5}
	ROUNDS=999999999
else
	ROUNDS=${1:-1}
fi

#=== ネタ収集 ===

collect_snippets() {
	snippets=()

	[[ -f best_score.txt ]] && snippets+=("BEST SCORE: $(cat best_score.txt) pts")

	if [[ -f score_history.txt ]] && [[ -s score_history.txt ]]; then
		local total=$(wc -l < score_history.txt | tr -d ' ')
		local avg=$(awk -F'\t' '{s+=$NF}END{printf "%.0f", s/NR}' score_history.txt)
		snippets+=("${total} games played / avg ${avg} pts")
		snippets+=("Last score: $(tail -1 score_history.txt | awk -F'\t' '{print $NF}') pts")
		snippets+=("Range: $(awk -F'\t' '{print $NF}' score_history.txt | sort -n | head -1) .. $(awk -F'\t' '{print $NF}' score_history.txt | sort -n | tail -1)")
	fi

	[[ -f game_count.txt ]] && snippets+=("Game #$(cat game_count.txt) and counting...")

	if [[ -f game_state.json ]]; then
		local info=$(python3 -c "
import json; d=json.load(open('game_state.json'))
print(f\"{len(d.get('pieces',[]))} pieces / {d.get('score',0)} pts / {d.get('state','?')}\")
" 2>/dev/null)
		[[ -n "$info" ]] && snippets+=("Board: $info")
	fi

	if [[ -f "$TMP_HISTORY_DIR/past_radio_topics.txt" ]] && [[ -s "$TMP_HISTORY_DIR/past_radio_topics.txt" ]]; then
		local lines=("${(@f)$(cat "$TMP_HISTORY_DIR/past_radio_topics.txt")}")
		(( ${#lines} > 0 )) && snippets+=("${lines[$((RANDOM % ${#lines} + 1))]}")
	fi

	if [[ -f tmp/radio_talk.txt ]] && [[ -s tmp/radio_talk.txt ]]; then
		local rlines=("${(@f)$(grep -v '^$' tmp/radio_talk.txt)}")
		if (( ${#rlines} > 0 )); then
			local rline="${rlines[$((RANDOM % ${#rlines} + 1))]}"
			(( ${#rline} > 80 )) && rline="${rline[1,77]}..."
			snippets+=("$rline")
		fi
	fi

	local latest_ver=$(ls -1t strategy_versions/v[0-9]*_strategy.py 2>/dev/null | head -1)
	[[ -n "$latest_ver" ]] && snippets+=("Strategy: $(basename $latest_ver)")

	local hall=$(ls -1 strategy_versions/best_score*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	(( hall > 0 )) && snippets+=("Hall of Fame: ${hall} strategies")

	local archives=$(ls -1 game_history/[0-9]*_score*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	(( archives > 0 )) && snippets+=("${archives} game logs archived")

	if [[ -f "$TMP_STATE_DIR/improve_state.json" ]]; then
		local imp=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/improve_state.json')).get('status','?'))" 2>/dev/null)
		snippets+=("Improve: ${imp}")
	fi

	if [[ -f "$TMP_STATE_DIR/accumulated_games.json" ]]; then
		local acc=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/accumulated_games.json')).get('count',0))" 2>/dev/null)
		(( acc > 0 )) && snippets+=("${acc} games queued for improvement")
	fi

	if [[ -f tmp/twitch_comments.txt ]] && [[ -s tmp/twitch_comments.txt ]]; then
		local clines=("${(@f)$(cat tmp/twitch_comments.txt)}")
		if (( ${#clines} > 0 )); then
			local cline="${clines[$((RANDOM % ${#clines} + 1))]}"
			snippets+=("Chat: $cline")
		fi
	fi

	if [[ -f tmp/news.txt ]] && [[ -s tmp/news.txt ]]; then
		local nlines=("${(@f)$(grep -v '^$' tmp/news.txt)}")
		if (( ${#nlines} > 0 )); then
			# 既読管理: news.txt が更新されたらリセット
			local news_mtime=$(stat -f %m tmp/news.txt 2>/dev/null)
			local shown_file="$TMP_STATE_DIR/.news_shown_lines.txt"
			local shown_mtime_file="$TMP_STATE_DIR/.news_shown_mtime.txt"
			local last_mtime=$(cat "$shown_mtime_file" 2>/dev/null)
			if [[ "$news_mtime" != "$last_mtime" ]]; then
				: > "$shown_file"
				echo "$news_mtime" > "$shown_mtime_file"
			fi
			local shown=$(cat "$shown_file" 2>/dev/null)
			# 未表示の行を探す
			local -a unseen=()
			for nline in "${nlines[@]}"; do
				if ! printf '%s\n' "$shown" | grep -qxF "$nline"; then
					unseen+=("$nline")
				fi
			done
			# 全部表示済みならリセット
			if (( ${#unseen} == 0 )); then
				: > "$shown_file"
				unseen=("${nlines[@]}")
			fi
			local nline="${unseen[$((RANDOM % ${#unseen} + 1))]}"
			echo "$nline" >> "$shown_file"
			(( ${#nline} > 80 )) && nline="${nline[1,77]}..."
			snippets+=("$nline")
		fi
	fi

	if [[ -f tmp/batch_summary.txt ]] && [[ -s tmp/batch_summary.txt ]]; then
		local blines=("${(@f)$(grep -v '^$' tmp/batch_summary.txt | grep -v '^===')}")
		if (( ${#blines} > 0 )); then
			local bline="${blines[$((RANDOM % ${#blines} + 1))]}"
			(( ${#bline} > 80 )) && bline="${bline[1,77]}..."
			snippets+=("$bline")
		fi
	fi

	if [[ -f score_history.txt ]] && (( $(wc -l < score_history.txt | tr -d ' ') >= 5 )); then
		local graph=$(tail -10 score_history.txt | python3 -c "
import sys
scores = [int(l.strip().split('\t')[-1]) for l in sys.stdin if l.strip().split('\t')[-1].isdigit()]
if scores:
    lo, hi = min(scores), max(scores)
    bars = '▁▂▃▄▅▆▇█'
    r = hi - lo if hi != lo else 1
    print(''.join(bars[min(int((s-lo)/r*7),7)] for s in scores) + ' last10')
" 2>/dev/null)
		[[ -n "$graph" ]] && snippets+=("$graph")
	fi
}

#=== 表示ヘルパー ===

random_cowsay_char() {
	local all=("${(@f)$(cowsay -l 2>/dev/null)}")
	if (( ${#all} > 0 )); then
		echo "${all[$((RANDOM % ${#all} + 1))]}"
	else
		echo "default"
	fi
}

is_ascii() {
	[[ "$1" == ${~:-[[:ascii:]]#} ]]
}

# 全角対応の枠表示
ascii_frame() {
	python3 -c "
import unicodedata, sys
def dw(s):
    return sum(2 if unicodedata.east_asian_width(c) in ('F','W') else 1 for c in s)
def wrap(text, w):
    out = []
    for raw in text.split('\n'):
        if dw(raw) <= w:
            out.append(raw); continue
        cur, cw = '', 0
        for ch in raw:
            chw = 2 if unicodedata.east_asian_width(ch) in ('F','W') else 1
            if cw + chw > w:
                out.append(cur); cur, cw = ch, chw
            else:
                cur += ch; cw += chw
        if cur: out.append(cur)
    return out
W = 58
lines = wrap(sys.stdin.read().rstrip('\n'), W)
b = '═' * (W + 2)
print(f'╔{b}╗')
for l in lines:
    print(f'║ {l}{\" \" * (W - dw(l))} ║')
print(f'╚{b}╝')
" <<< "$1"
}

#=== データ表示レンダラー ===

render_snippet() {
	local text="$1"

	local renderers=()
	(( $+commands[cowsay] )) && renderers+=("cowsay")
	(( $+commands[figlet] )) && is_ascii "$text" && renderers+=("figlet")
	(( $+commands[boxes] ))  && renderers+=("boxes")
	(( $+commands[lolcat] )) && renderers+=("lolcat")
	renderers+=("ascii_frame")

	local pick="${renderers[$((RANDOM % ${#renderers} + 1))]}"

	case "$pick" in
	cowsay)
		local char=$(random_cowsay_char)
		if (( $+commands[lolcat] )) && (( RANDOM % 2 == 0 )); then
			echo "$text" | cowsay -f "$char" 2>/dev/null | lolcat 2>/dev/null || ascii_frame "$text"
		else
			echo "$text" | cowsay -f "$char" 2>/dev/null || ascii_frame "$text"
		fi
		;;
	figlet)
		local short="$text"
		(( ${#short} > 30 )) && short="${short[1,27]}..."
		if (( $+commands[lolcat] )) && (( RANDOM % 2 == 0 )); then
			figlet "$short" 2>/dev/null | lolcat 2>/dev/null || ascii_frame "$text"
		else
			figlet "$short" 2>/dev/null || ascii_frame "$text"
		fi
		;;
	boxes)
		local avail=("${(@f)$(boxes -l 2>/dev/null | grep '^ *[a-z]' | awk '{print $1}' | head -20)}")
		if (( ${#avail} > 0 )); then
			echo "$text" | boxes -d "${avail[$((RANDOM % ${#avail} + 1))]}" 2>/dev/null || ascii_frame "$text"
		else
			ascii_frame "$text"
		fi
		;;
	lolcat)
		ascii_frame "$text" | lolcat 2>/dev/null
		;;
	*)
		ascii_frame "$text"
		;;
	esac
}

#=== fortune 表示 (cowsay/boxes/lolcat と組み合わせ) ===

show_fortune() {
	(( ! $+commands[fortune] )) && return 1

	local styles=()
	(( $+commands[cowsay] )) && styles+=("cowsay")
	(( $+commands[boxes] ))  && styles+=("boxes")
	(( $+commands[lolcat] )) && styles+=("lolcat")
	# 組み合わせ
	(( $+commands[cowsay] && $+commands[lolcat] )) && styles+=("cowsay_lolcat")
	(( $+commands[boxes] && $+commands[lolcat] ))  && styles+=("boxes_lolcat")
	(( ${#styles} == 0 )) && styles+=("plain")

	local style="${styles[$((RANDOM % ${#styles} + 1))]}"
	local f=$(fortune -s 2>/dev/null) || return 1

	case "$style" in
	cowsay)
		echo "$f" | cowsay -f "$(random_cowsay_char)" 2>/dev/null
		;;
	cowsay_lolcat)
		echo "$f" | cowsay -f "$(random_cowsay_char)" 2>/dev/null | lolcat 2>/dev/null
		;;
	boxes)
		local avail=("${(@f)$(boxes -l 2>/dev/null | grep '^ *[a-z]' | awk '{print $1}' | head -20)}")
		if (( ${#avail} > 0 )); then
			echo "$f" | boxes -d "${avail[$((RANDOM % ${#avail} + 1))]}" 2>/dev/null
		else
			echo "$f"
		fi
		;;
	boxes_lolcat)
		local avail=("${(@f)$(boxes -l 2>/dev/null | grep '^ *[a-z]' | awk '{print $1}' | head -20)}")
		if (( ${#avail} > 0 )); then
			echo "$f" | boxes -d "${avail[$((RANDOM % ${#avail} + 1))]}" 2>/dev/null | lolcat 2>/dev/null
		else
			echo "$f" | lolcat 2>/dev/null
		fi
		;;
	lolcat)
		echo "$f" | lolcat 2>/dev/null
		;;
	*)
		echo "$f"
		;;
	esac
}

#=== sl 全オプション組み合わせ ===

show_sl() {
	(( ! $+commands[sl] )) && return 1

	# -a(accident) -l(little) -F(flying) -c(C51) の全組み合わせ = 16通り
	local sl_combos=(
		""
		"-a"
		"-l"
		"-F"
		"-c"
		"-al"
		"-aF"
		"-ac"
		"-lF"
		"-lc"
		"-Fc"
		"-alF"
		"-alc"
		"-aFc"
		"-lFc"
		"-alFc"
	)
	local opts="${sl_combos[$((RANDOM % ${#sl_combos} + 1))]}"
	sl ${opts} 2>/dev/null </dev/null || true
}

#=== フルスクリーン系コマンド ===

show_fullscreen() {
	local cmds=()
	(( $+commands[nyancat] ))   && cmds+=("nyancat")
	(( $+commands[cmatrix] ))       && cmds+=("cmatrix")
	(( $+commands[tty-clock] ))     && cmds+=("tty-clock")
	(( $+commands[asciiquarium] ))  && cmds+=("asciiquarium")
	(( $+commands[genact] ))        && cmds+=("genact")
	(( ${#cmds} == 0 )) && return 1

	local cmd="${cmds[$((RANDOM % ${#cmds} + 1))]}"

	# TERM=vt100 で代替バッファ (smcup/rmcup) を無効化し、メイン画面に直接描画させる
	case "$cmd" in
	nyancat)
		TERM=vt100 timeout 10 nyancat 2>/dev/null || true
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
	asciiquarium)
		timeout 10 asciiquarium 2>/dev/null || true
		printf '\033[0m\n'
		;;
	genact)
		timeout 12 genact 2>/dev/null || true
		;;
	esac
}

#=== メイン: 何を表示するか抽選 ===

# 利用可能なアクション一覧を構築
build_actions() {
	actions=("data")  # データ表示は常にある

	(( $+commands[fortune] )) && actions+=("fortune")
	(( $+commands[sl] ))      && actions+=("sl")

	# フルスクリーン系が1つでもあれば
	local has_fs=false
	(( $+commands[nyancat] ))   && has_fs=true
	(( $+commands[cmatrix] ))   && has_fs=true
	(( $+commands[tty-clock] )) && has_fs=true
	(( $+commands[genact] ))    && has_fs=true
	$has_fs && actions+=("fullscreen")
}

do_one_round() {
	build_actions

	# 均等抽選
	local action="${actions[$((RANDOM % ${#actions} + 1))]}"

	case "$action" in
	data)
		collect_snippets
		(( ${#snippets} == 0 )) && return

		local pick_count=$(( RANDOM % 3 + 1 ))
		(( pick_count > ${#snippets} )) && pick_count=${#snippets}

		local selected=() used=() _p=0
		while (( _p < pick_count )); do
			local idx=$(( RANDOM % ${#snippets} + 1 ))
			if (( ! ${used[(Ie)$idx]} )); then
				used+=($idx)
				selected+=("${snippets[$idx]}")
				(( _p++ ))
			fi
		done

		local combined="${(pj:\n:)selected}"
		render_snippet "$combined" 2>/dev/null | grep -v "^Try \`"
		;;
	fortune)
		show_fortune 2>/dev/null | grep -v "^Try \`"
		;;
	sl)
		show_sl
		;;
	fullscreen)
		show_fullscreen
		;;
	esac
}

#=== 実行 ===

local _round=0
while (( _round < ROUNDS )); do
	(( _round++ ))

	do_one_round

	if $LOOP_MODE; then
		sleep "$LOOP_INTERVAL"
	elif (( _round < ROUNDS )); then
		sleep 2
	fi
done
exit 0
