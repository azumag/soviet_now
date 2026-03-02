#!/bin/zsh
# show_random_info.sh - eloopの出力からランダムにピックアップして面白く表示
#
# Usage: ./show_random_info.sh [回数(default:1)]

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

ROUNDS=${1:-1}

#=== ネタ収集 ===

collect_snippets() {
	snippets=()

	# 1) best_score.txt
	if [[ -f best_score.txt ]]; then
		snippets+=("BEST SCORE: $(cat best_score.txt) pts")
	fi

	# 2) score_history.txt
	if [[ -f score_history.txt ]] && [[ -s score_history.txt ]]; then
		local total=$(wc -l < score_history.txt | tr -d ' ')
		local avg=$(awk '{s+=$1}END{printf "%.0f", s/NR}' score_history.txt)
		local last=$(tail -1 score_history.txt)
		snippets+=("${total} games played / avg ${avg} pts")
		snippets+=("Last score: ${last} pts")
		local hi=$(sort -n score_history.txt | tail -1)
		local lo=$(sort -n score_history.txt | head -1)
		snippets+=("Range: ${lo} .. ${hi}")
	fi

	# 3) game_count.txt
	if [[ -f game_count.txt ]]; then
		snippets+=("Game #$(cat game_count.txt) and counting...")
	fi

	# 4) game_state.json
	if [[ -f game_state.json ]]; then
		local info=$(python3 -c "
import json
d=json.load(open('game_state.json'))
print(f\"{len(d.get('pieces',[]))} pieces / {d.get('score',0)} pts / {d.get('state','?')}\")
" 2>/dev/null)
		[[ -n "$info" ]] && snippets+=("Board: $info")
	fi

	# 5) past_radio_topics.txt からランダム1行
	if [[ -f tmp/past_radio_topics.txt ]] && [[ -s tmp/past_radio_topics.txt ]]; then
		local lines=("${(@f)$(cat tmp/past_radio_topics.txt)}")
		(( ${#lines} > 0 )) && snippets+=("${lines[$((RANDOM % ${#lines} + 1))]}")
	fi

	# 6) radio_talk.txt からランダム抜粋
	if [[ -f tmp/radio_talk.txt ]] && [[ -s tmp/radio_talk.txt ]]; then
		local rlines=("${(@f)$(grep -v '^$' tmp/radio_talk.txt)}")
		if (( ${#rlines} > 0 )); then
			local rline="${rlines[$((RANDOM % ${#rlines} + 1))]}"
			(( ${#rline} > 80 )) && rline="${rline[1,77]}..."
			snippets+=("$rline")
		fi
	fi

	# 7) strategy_versions/ 最新バージョン
	local latest_ver=$(ls -1t strategy_versions/v[0-9]*_strategy.py 2>/dev/null | head -1)
	[[ -n "$latest_ver" ]] && snippets+=("Strategy: $(basename $latest_ver)")

	# 8) 殿堂入り数
	local hall=$(ls -1 strategy_versions/best_score*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	(( hall > 0 )) && snippets+=("Hall of Fame: ${hall} strategies")

	# 9) アーカイブ数
	local archives=$(ls -1 game_history/[0-9]*_score*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	(( archives > 0 )) && snippets+=("${archives} game logs archived")

	# 10) improve_state.json
	if [[ -f tmp/improve_state.json ]]; then
		local imp=$(python3 -c "import json; print(json.load(open('tmp/improve_state.json')).get('status','?'))" 2>/dev/null)
		snippets+=("Improve: ${imp}")
	fi

	# 11) accumulated_games.json
	if [[ -f tmp/accumulated_games.json ]]; then
		local acc=$(python3 -c "import json; print(json.load(open('tmp/accumulated_games.json')).get('count',0))" 2>/dev/null)
		(( acc > 0 )) && snippets+=("${acc} games queued for improvement")
	fi

	# 12) twitch_comments.txt からランダム
	if [[ -f tmp/twitch_comments.txt ]] && [[ -s tmp/twitch_comments.txt ]]; then
		local clines=("${(@f)$(cat tmp/twitch_comments.txt)}")
		if (( ${#clines} > 0 )); then
			local cline="${clines[$((RANDOM % ${#clines} + 1))]}"
			(( ${#cline} > 80 )) && cline="${cline[1,77]}..."
			snippets+=("Chat: $cline")
		fi
	fi

	# 13) news.txt からランダム
	if [[ -f tmp/news.txt ]] && [[ -s tmp/news.txt ]]; then
		local nlines=("${(@f)$(grep -v '^$' tmp/news.txt)}")
		if (( ${#nlines} > 0 )); then
			local nline="${nlines[$((RANDOM % ${#nlines} + 1))]}"
			(( ${#nline} > 80 )) && nline="${nline[1,77]}..."
			snippets+=("$nline")
		fi
	fi

	# 14) batch_summary.txt から1行
	if [[ -f tmp/batch_summary.txt ]] && [[ -s tmp/batch_summary.txt ]]; then
		local blines=("${(@f)$(grep -v '^$' tmp/batch_summary.txt | grep -v '^===')}")
		if (( ${#blines} > 0 )); then
			local bline="${blines[$((RANDOM % ${#blines} + 1))]}"
			(( ${#bline} > 80 )) && bline="${bline[1,77]}..."
			snippets+=("$bline")
		fi
	fi

	# 15) スコア推移ミニグラフ
	if [[ -f score_history.txt ]] && (( $(wc -l < score_history.txt | tr -d ' ') >= 5 )); then
		local graph=$(tail -10 score_history.txt | python3 -c "
import sys
scores = [int(l.strip()) for l in sys.stdin if l.strip().isdigit()]
if scores:
    lo, hi = min(scores), max(scores)
    bars = '▁▂▃▄▅▆▇█'
    r = hi - lo if hi != lo else 1
    print(''.join(bars[min(int((s-lo)/r*7),7)] for s in scores) + ' last10')
" 2>/dev/null)
		[[ -n "$graph" ]] && snippets+=("$graph")
	fi
}

#=== 表示コマンド ===

detect_renderers() {
	renderers=()
	(( $+commands[cowsay] )) && renderers+=("cowsay")
	(( $+commands[toilet] )) && renderers+=("toilet")
	(( $+commands[figlet] )) && renderers+=("figlet")
	(( $+commands[boxes] ))  && renderers+=("boxes")
	(( $+commands[lolcat] )) && renderers+=("lolcat")
	renderers+=("ascii_frame")
}

random_cowsay_char() {
	local chars=("${(@f)$(cowsay -l 2>/dev/null | tail -n +2)}")
	# cowsay -l はスペース区切りなので展開
	local all=( ${=chars} )
	if (( ${#all} > 0 )); then
		echo "${all[$((RANDOM % ${#all} + 1))]}"
	else
		echo "default"
	fi
}

ascii_frame() {
	local text="$1"
	local w=60
	local border=$(printf '═%.0s' {1..$((w + 2))})
	echo "╔${border}╗"
	echo "$text" | fold -s -w $w | while IFS= read -r line; do
		printf '║ %-'${w}'s ║\n' "$line"
	done
	echo "╚${border}╝"
}

render_snippet() {
	local text="$1"
	detect_renderers
	local pick="${renderers[$((RANDOM % ${#renderers} + 1))]}"

	case "$pick" in
	cowsay)
		local char=$(random_cowsay_char)
		if (( $+commands[lolcat] )) && (( RANDOM % 2 == 0 )); then
			echo "$text" | cowsay -f "$char" 2>/dev/null | lolcat 2>/dev/null
		else
			echo "$text" | cowsay -f "$char" 2>/dev/null
		fi
		;;
	toilet)
		local short="$text"
		(( ${#short} > 30 )) && short="${short[1,27]}..."
		local fonts=("big" "standard" "small" "smslant" "mini" "future" "pagga")
		local filters=("--gay" "--metal" "" "--border")
		local font="${fonts[$((RANDOM % ${#fonts} + 1))]}"
		local filter="${filters[$((RANDOM % ${#filters} + 1))]}"
		toilet -f "$font" ${=filter} "$short" 2>/dev/null || echo "$text"
		;;
	figlet)
		local short="$text"
		(( ${#short} > 30 )) && short="${short[1,27]}..."
		if (( $+commands[lolcat] )) && (( RANDOM % 2 == 0 )); then
			figlet "$short" 2>/dev/null | lolcat 2>/dev/null
		else
			figlet "$short" 2>/dev/null
		fi
		;;
	boxes)
		# 利用可能なデザインからランダム選択
		local avail_designs=("${(@f)$(boxes -l 2>/dev/null | grep '^ *[a-z]' | awk '{print $1}' | head -20)}")
		if (( ${#avail_designs} > 0 )); then
			local design="${avail_designs[$((RANDOM % ${#avail_designs} + 1))]}"
			echo "$text" | boxes -d "$design" 2>/dev/null || ascii_frame "$text"
		else
			ascii_frame "$text"
		fi
		;;
	lolcat)
		ascii_frame "$text" | lolcat 2>/dev/null
		;;
	ascii_frame)
		ascii_frame "$text"
		;;
	esac
}

#=== 実行 ===

collect_snippets

if (( ${#snippets} == 0 )); then
	echo "No data found. Run some games first!"
	exit 0
fi

for round in {1..$ROUNDS}; do
	# 毎ラウンド再収集（ランダム要素入りのネタが変わる）
	collect_snippets

	# 1〜3個ランダムピック
	local pick_count=$(( RANDOM % 3 + 1 ))
	(( pick_count > ${#snippets} )) && pick_count=${#snippets}

	# 重複なしでピック
	local selected=()
	local used=()
	for i in {1..$pick_count}; do
		local attempts=0
		while (( attempts < 20 )); do
			local idx=$(( RANDOM % ${#snippets} + 1 ))
			if (( ! ${used[(Ie)$idx]} )); then
				used+=($idx)
				selected+=("${snippets[$idx]}")
				break
			fi
			(( attempts++ ))
		done
	done

	# 結合して表示
	local combined="${(j:\n:)selected}"
	render_snippet "$combined"

	# 複数ラウンドなら間を空ける
	(( ROUNDS > 1 && round < ROUNDS )) && sleep 3
done
