#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# commands.txt の内容変更を検知してループ実行

# .env から環境変数を読み込み（exportはしない）
if [ -f "$SCRIPT_DIR/.env" ]; then
	source "$SCRIPT_DIR/.env"
fi

prev_commands=""

while true; do
	current_commands=$(cat "./commands.txt" 2>/dev/null || echo "")
	if [ "$current_commands" = "$prev_commands" ]; then
		PROMPT=$(cat "./PROMPT.md" 2>/dev/null || echo "")
		OUTPUT=$(ANTHROPIC_BASE_URL=http://localhost:8787 ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.7" claude -p "'$PROMPT'" --model=Haiku --permission-mode=acceptEdits 2>&1)
		echo "$OUTPUT"
		if echo "$OUTPUT" | grep -q "API Error: Rate limit reached"; then
			echo "Rate limit detected, falling back to flash..."
			# opencode run "'$PROMPT'" --agent='zai-v'
			ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.7-flash" claude -p "'$PROMPT'" --model=Haiku --permission-mode=acceptEdits
		fi
	else
		echo "commands.txt が更新されたためスキップします"
	fi
	prev_commands="$current_commands"
	sleep 10
done
