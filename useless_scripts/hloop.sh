#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="$SCRIPT_DIR/soviet_now.png"
LAST_HASH=""

while true; do
	# soviet_now.png のハッシュ値を取得
	CURRENT_HASH=$(md5 -q "$TARGET" 2>/dev/null || echo "")

	if [ "$CURRENT_HASH" = "$LAST_HASH" ]; then
		# 更新されていない → 実行
		PROMPT=$(cat "./PROMPT_US.md" 2>/dev/null || echo "")
		ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5-20251001" claude -p "'$PROMPT'" --model=Haiku --permission-mode=acceptEdits
	else
		# 更新された → スキップ
		EC="soviet_now.png が更新されたためスキップします"
	fi

	LAST_HASH="$CURRENT_HASH"
	sleep 30
done
