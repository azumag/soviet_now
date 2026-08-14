#!/usr/bin/env bash
# manual_improve_on.sh — 手動改善モードを有効化
# 次の改善サイクル到達時に eloop_improve.sh を起動せず、手動編集待ちになる
set -euo pipefail

# .env をロード
cd "$(dirname "${BASH_SOURCE[0]}")"
[ -f .env ] && set -a && . ./.env && set +a

STATE_DIR="tmp/state"
FLAG_FILE="$STATE_DIR/manual_improve_mode"
IMPROVE_STATE_FILE="$STATE_DIR/improve_state.json"

mkdir -p "$STATE_DIR"
touch "$FLAG_FILE"

echo "[manual_improve] 手動改善モード: ON"
echo "[manual_improve] フラグ: $FLAG_FILE"

if [ -f "$IMPROVE_STATE_FILE" ]; then
    current_status=$(python3 -c "import json,sys; print(json.load(open('$IMPROVE_STATE_FILE')).get('status','idle'))" 2>/dev/null || echo "unknown")
    echo "[manual_improve] 現在の改善状態: $current_status"
fi

if [ "${IMPROVE_KEEP_MAIN_GAME_RUNNING:-0}" = "1" ]; then
    echo "[manual_improve] 次の改善タイミング後もメインゲームは継続します。"
else
    echo "[manual_improve] 次の改善タイミング(12ゲーム後)でメインループが一時停止します。"
fi
echo "[manual_improve] strategy.py を編集したら ./manual_improve_off.sh で改善完了を反映してください。"
