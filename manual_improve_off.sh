#!/usr/bin/env bash
# manual_improve_off.sh — 手動改善を完了してメインループを再開
# improve_state.json の status が "manual" の場合、harvest処理を行ってidleに戻す
set -euo pipefail

# eloop_lib.sh をsourceして共通関数を使用
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source ./eloop_lib.sh

STATE_DIR="tmp/state"
FLAG_FILE="$STATE_DIR/manual_improve_mode"
IMPROVE_STATE_FILE_LOCAL="$STATE_DIR/improve_state.json"

# status確認
if [ ! -f "$IMPROVE_STATE_FILE_LOCAL" ]; then
    echo "[manual_improve] improve_state.json が存在しません。手動改善モードは有効ではありません。"
    rm -f "$FLAG_FILE"
    exit 0
fi

current_status=$(python3 -c "import json,sys; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('status','idle'))" 2>/dev/null || echo "idle")

if [ "$current_status" != "manual" ]; then
    echo "[manual_improve] 現在の状態: $current_status (手動改善待ちではありません)"
    rm -f "$FLAG_FILE"
    echo "[manual_improve] フラグを削除しました。次の改善サイクルから自動改善に戻ります。"
    exit 0
fi

echo "[manual_improve] 手動改善完了処理を開始します..."

# strategy_hash_before と現在のハッシュを比較
hash_before=$(python3 -c "import json,sys; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('strategy_hash_before',''))" 2>/dev/null || echo "")
hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

echo "[manual_improve] hash_before: ${hash_before:-（未記録）}"
echo "[manual_improve] hash_now:    ${hash_now}"

if [ "$hash_before" != "$hash_now" ]; then
    echo "[manual_improve] 戦略更新を検出: $hash_before -> $hash_now"
    log "[IMPROVE][MANUAL] 戦略更新検出: $hash_before -> $hash_now"

    # ローリングスコア: 新戦略のprev_hashを記録
    new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
    prev_decide_hash=""
    if [ -f "tmp/revert_strategy.py" ]; then
        prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
    fi
    if [ -z "$prev_decide_hash" ] && [ -f "$CURRENT_STRATEGY_RUN_FILE" ]; then
        prev_decide_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")
    fi
    if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
        python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
    fi

    # ブランチ遷移
    if [ -n "$new_decide_hash" ]; then
        branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" 2>/dev/null || true)
        [ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
        _reset_current_strategy_run "$new_decide_hash"
    fi

    # 蓄積データは旧戦略のものなので破棄
    acc_count_discarded=0
    if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
        acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
    fi
    _clear_accumulated_data
    if [ "${acc_count_discarded:-0}" -gt 0 ]; then
        log "[IMPROVE][MANUAL] 蓄積${acc_count_discarded}試合を破棄 (旧戦略のデータ)"
    fi

    # バージョン保存
    save_strategy_version "manual_improve" 2>/dev/null || true

    _write_improve_state "idle" "0" "" "" "0" ""
    rm -f "$TMP_STATE_DIR/last_improve_failed_at"
    echo "[manual_improve] 改善成功として記録しました。"
else
    echo "[manual_improve] strategy.py に変更なし → failed_no_apply として記録"
    log "[IMPROVE][MANUAL] 戦略変更なし (手動改善OFFのみ)"
    date +%s > "$TMP_STATE_DIR/last_improve_failed_at"
    _write_improve_state "idle" "0" "" "failed_no_apply" "100" "manual_no_change"
fi

# フラグ削除
rm -f "$FLAG_FILE"

# OBS: 改善中コンソール非表示
./obs_control.sh hide soren console4 2>/dev/null &

# soren91 停止
if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
    log "[IMPROVE][MANUAL] manual_meriken_mode=on のため、メリケンAI継続"
elif [ "$(date +%H)" = "20" ]; then
    log "[IMPROVE][MANUAL] 20時台: メリケンAIタイムに移行 → soren91継続"
    soren91_improve 2>/dev/null || true
else
    soren91_stop 2>/dev/null || true
    soren91_improve 2>/dev/null || true
fi

log "[IMPROVE][MANUAL] 手動改善完了 → idle"
echo "[manual_improve] 手動改善モード: OFF → メインループが再開します。"
