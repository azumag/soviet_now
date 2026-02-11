#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# commands.txt の内容変更を検知してループ実行

prev_commands=""

while true; do
	current_commands=$(cat "./commands.txt" 2>/dev/null || echo "")
	if [ "$current_commands" = "$prev_commands" ]; then
		PROMPT=$(cat "./PROMPT.md" 2>/dev/null || echo "")
		# opencode run "'$PROMPT'" --agent='zai-v'
		ANTHROPIC_BASE_URL=http://localhost:8787 ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.7" claude -p "'$PROMPT'" --model=Haiku --permission-mode=bypassPermissions
	else
		echo "commands.txt が更新されたためスキップします"
	fi
	prev_commands="$current_commands"
	sleep 10
done
