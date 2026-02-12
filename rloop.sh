#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# soviet_now.png の内容変更を検知してループ実行

# .env から環境変数を読み込み（exportはしない）
if [ -f "$SCRIPT_DIR/.env" ]; then
	source "$SCRIPT_DIR/.env"
fi

TARGET="$SCRIPT_DIR/soviet_now.png"
prev_hash=""

while true; do
	current_hash=$(md5 -q "$TARGET" 2>/dev/null || echo "")
	if [ "$current_hash" = "$prev_hash" ]; then
		PROMPT=$(cat "./PROMPT.md" 2>/dev/null || echo "")
		OUTPUT=$(ANTHROPIC_BASE_URL=http://localhost:8787 ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.7" claude -p "'$PROMPT'" --model=Haiku --permission-mode=acceptEdits 2>&1)
		echo "$OUTPUT"
		if echo "$OUTPUT" | grep -q "API Error: Rate limit reached"; then
			echo "Rate limit detected, falling back to flash..."
			# opencode run "'$PROMPT'" --agent='zai-v'
			ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.7-flash" claude -p "'$PROMPT'" --model=Haiku --permission-mode=acceptEdits
		fi
	else
		# echo "soviet_now.png が更新されたためスキップします"
		EC="soviet_now.png が更新されたためスキップします"
	fi
	prev_hash="$current_hash"
	sleep 10
done
