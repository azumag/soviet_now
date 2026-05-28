#!/usr/bin/env bash
# Test classifier with example comments for each category

set -e

ELOOP_LIB_DIR="${ELOOP_LIB_DIR:-$(cd "$(dirname "$0")" && pwd)}"
source "$ELOOP_LIB_DIR/eloop_lib.sh" 2>/dev/null || true

MODEL="${COMMENT_CLASSIFIER_AGENT:-minimax}"

declare -A TEST_COMMENTS=(
	["card_gacha"]="azumagbanjo: ユーザーがAを獲得しました"
	["raid"]="nightbot: さんがレイジを迎えます！ from twitchuser123"
	["subscription"]="Thanks for the subscription twitchfan!"
	["bits"]="Kappa 100 bits"
	["sing_request"]="きらきら星歌って"
	["game_question"]="ゲームのマージってどうやるの？"
	["game_status"]="スコア上がった？"
	["general_question"]="今日の天気教えて"
	["strategy_advice"]="大型ピースは片側にまとめるべき"
	["comment_advice"]="コメントもっと短くして"
	["stream_bug_report"]="配信画面が止まって音声も出てない"
	["chitchat_short"]="なるほど"
	["chitchat"]=" 오늘 뭐 해?"
	["other"]="hello world"
)

echo "=== Classifier Test ==="
echo ""

for category in card_gacha raid subscription bits sing_request game_question game_status general_question strategy_advice comment_advice stream_bug_report chitchat_short chitchat other; do
	comment="${TEST_COMMENTS[$category]}"
	echo "--- $category ---"
	echo "Input: $comment"

	tmpfile=$(mktemp /tmp/test_classifier_XXXXXX)
	printf '%s\n' "$comment" >"$tmpfile"

	result=$(_classify_comments "$tmpfile" 2>/dev/null | tr -d '\n' | head -c 500)
	rm -f "$tmpfile"

	echo "Output: $result"

	# Validate JSON
	if [ -n "$result" ]; then
		is_valid=$(printf '%s' "$result" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list) and len(data) > 0:
        print('valid')
    else:
        print('invalid')
except Exception:
    print('invalid')
" 2>/dev/null)
		echo "Valid JSON: $is_valid"
		if [ "$is_valid" = "valid" ]; then
			parsed=$(printf '%s' "$result" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for item in data:
    print(f\"  -> category: {item.get('category', '?')}, comment: {item.get('comment', '?')[:50]}\")
" 2>/dev/null)
			echo "$parsed"
		fi
	fi
	echo ""
done

echo "=== Full Comment Response Test ==="
echo ""

for category in chitchat game_question card_gacha; do
	comment="${TEST_COMMENTS[$category]}"
	echo "--- $category ---"
	echo "Input: $comment"

	# Create batch file
	tmpfile=$(mktemp /tmp/test_comments_XXXXXX)
	echo "$comment" >"$tmpfile"

	# Run classifier
	classification=$(_classify_comments "$tmpfile" 2>/dev/null)
	rm -f "$tmpfile"

	echo "Classification: $classification"

	if [ -n "$classification" ]; then
		dominant=$(printf '%s' "$classification" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
counts = {}
for item in data:
    cat = item.get('category', 'chitchat')
    counts[cat] = counts.get(cat, 0) + 1
total = len(data)
dominant = max(counts, key=counts.get)
ratio = counts[dominant] / total
if ratio > 0.8:
    print(dominant)
else:
    if len(counts) == 1:
        print(list(counts.keys())[0])
    else:
        print('mixed')
" 2>/dev/null)
		echo "Dominant: $dominant"

		# Build prompt
		promptfile=$(mktemp /tmp/test_prompt_XXXXXX)
		formatted=$(printf '%s' "$classification" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for item in data:
    idx = item.get('index', 0)
    user = item.get('user', '')
    comment = item.get('comment', '')
    cat = item.get('category', 'chitchat')
    print(f'[{idx}] {user}: {comment} -> {cat}')
" 2>/dev/null)

		_build_category_prompt "$dominant" "$comment" "$formatted" "$promptfile" 2>/dev/null

		if [ -s "$promptfile" ]; then
			echo "Prompt generated: $(wc -l <"$promptfile") lines"
			echo "--- Prompt Preview ---"
			head -20 "$promptfile"
			echo "..."
		else
			echo "Prompt generation FAILED (empty file)"
		fi
		rm -f "$promptfile"
	fi
	echo ""
done

echo "=== Done ==="
