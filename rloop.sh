#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# commands.txt の内容変更を検知してループ実行

# フォールバック用: commands.txt の前回内容と連続未変更回数を追跡
prev_commands=""
same_count=0
FALLBACK_THRESHOLD_1=10
FALLBACK_THRESHOLD_2=20
fallback_level=0  # 0=通常, 1=入替, 2=glmflash

while true; do
	# commands.txt の内容を読み込み、変更検知でフォールバック判定（3段階）
	current_commands=$(cat "./commands.txt" 2>/dev/null || echo "")
	if [ "$current_commands" = "$prev_commands" ]; then
		same_count=$((same_count + 1))
	else
		same_count=1
		fallback_level=0
	fi
	prev_commands="$current_commands"

	if [ "$same_count" -ge "$FALLBACK_THRESHOLD_2" ] && [ "$fallback_level" -lt 2 ]; then
		fallback_level=2
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠⚠ commands.txt が ${same_count} 回未変更 → glmflash に切り替え"
	elif [ "$same_count" -ge "$FALLBACK_THRESHOLD_1" ] && [ "$fallback_level" -lt 1 ]; then
		fallback_level=1
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠ commands.txt が ${same_count} 回未変更 → エージェント入替フォールバック"
	fi

	# フォールバックレベルに応じてエージェントを切り替え
	agent=""
	if [ "$fallback_level" -eq 2 ]; then
		agent="--agent='glmflash'"
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] 実行 (フォールバック Lv2: glmflash)"
	elif [ "$fallback_level" -eq 1 ]; then
		agent="--agent='zai'"
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] 実行 (フォールバック Lv1: zai)"
	else
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] 実行"
	fi

	PROMPT=$(cat "./PROMPT.md" 2>/dev/null || echo "")
	opencode run "'$PROMPT'" $agent

	sleep 10
done
