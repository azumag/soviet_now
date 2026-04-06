#!/usr/bin/env bash
# manual_improve_off.sh — 手動改善完了を通知してメインループを再開
# フラグを削除するだけ。soren91停止等の後処理はメインループの
# check_and_harvest_improvement() が安全に行う。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_DIR="tmp/state"
FLAG_FILE="$STATE_DIR/manual_improve_mode"
IMPROVE_STATE_FILE_LOCAL="$STATE_DIR/improve_state.json"

# status確認
if [ ! -f "$IMPROVE_STATE_FILE_LOCAL" ]; then
    echo "[manual_improve] improve_state.json が存在しません。"
    rm -f "$FLAG_FILE"
    exit 0
fi

current_status=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('status','idle'))" 2>/dev/null || echo "idle")

if [ "$current_status" != "manual" ]; then
    echo "[manual_improve] 現在の状態: $current_status (手動改善待ちではありません)"
    rm -f "$FLAG_FILE"
    exit 0
fi

# ハッシュ比較 (情報表示のみ)
hash_before=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('strategy_hash_before',''))" 2>/dev/null || echo "")
hash_now=$(md5 -q strategy.py 2>/dev/null | cut -c1-8)
echo "[manual_improve] hash_before: ${hash_before:-（未記録）}"
echo "[manual_improve] hash_now:    ${hash_now}"
if [ "$hash_before" != "$hash_now" ]; then
    echo "[manual_improve] 戦略変更あり → メインループが改善成功として記録します"
else
    echo "[manual_improve] 戦略変更なし → failed_no_apply として記録されます"
fi

# フラグ削除 — これだけでメインループが処理を引き継ぐ
rm -f "$FLAG_FILE"
echo "[manual_improve] フラグ削除完了。メインループが次の3秒以内にharvest処理を実行します。"
