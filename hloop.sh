#!/bin/bash

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="$SCRIPT_DIR/soviet_now.png"
LAST_MOD=""

while true; do
	# soviet_now.png の最終更新時刻を取得
	CURRENT_MOD=$(stat -f "%m" "$TARGET" 2>/dev/null || echo "0")

	if [ "$CURRENT_MOD" = "$LAST_MOD" ]; then
		# 更新されていない → 実行
		PROMPT=$(cat "./PROMPT.md" 2>/dev/null || echo "")
		ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5-20251001" claude -p "'$PROMPT'" --model=Haiku --permission-mode=bypassPermissions
	else
		# 更新された → スキップ
		echo $CURRENT_MOD
		echo $LAST_MOD
		echo "soviet_now.png が更新されたためスキップします"
	fi

	LAST_MOD="$CURRENT_MOD"
	sleep 10
done
