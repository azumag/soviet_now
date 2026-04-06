#!/usr/bin/env bash
# manual_improve_off.sh — 手動改善を完了してメインループを再開
# 注: フラグ削除・状態リセットをeloop_lib.shのsource前に実行して確実に動くようにしている

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_DIR="tmp/state"
FLAG_FILE="$STATE_DIR/manual_improve_mode"
IMPROVE_STATE_FILE_LOCAL="$STATE_DIR/improve_state.json"

# status確認
if [ ! -f "$IMPROVE_STATE_FILE_LOCAL" ]; then
    echo "[manual_improve] improve_state.json が存在しません。手動改善モードは有効ではありません。"
    rm -f "$FLAG_FILE"
    exit 0
fi

current_status=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('status','idle'))" 2>/dev/null || echo "idle")

if [ "$current_status" != "manual" ]; then
    echo "[manual_improve] 現在の状態: $current_status (手動改善待ちではありません)"
    rm -f "$FLAG_FILE"
    echo "[manual_improve] フラグを削除しました。次の改善サイクルから自動改善に戻ります。"
    exit 0
fi

echo "[manual_improve] 手動改善完了処理を開始します..."

# ハッシュ比較
hash_before=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE_LOCAL')).get('strategy_hash_before',''))" 2>/dev/null || echo "")
hash_now=$(md5 -q strategy.py 2>/dev/null | cut -c1-8)
echo "[manual_improve] hash_before: ${hash_before:-（未記録）}"
echo "[manual_improve] hash_now:    ${hash_now}"

# ── 最優先: フラグ削除・状態リセット (eloop_lib.sh不要、直接ファイル操作) ──
rm -f "$FLAG_FILE"

if [ "$hash_before" != "$hash_now" ]; then
    python3 - "$IMPROVE_STATE_FILE_LOCAL" <<'PY'
import json, sys, time
f = sys.argv[1]
now = int(time.time())
with open(f, 'w') as fp:
    json.dump({'status':'idle','pid':0,'strategy_hash_before':'','phase':'','progress':0,'detail':'','started_at':0,'updated_at':now}, fp)
PY
    rm -f "$STATE_DIR/last_improve_failed_at"
    echo "[manual_improve] 戦略更新を検出: $hash_before -> $hash_now"
    echo "[manual_improve] 改善成功として記録。メインループが再開します。"
else
    python3 - "$IMPROVE_STATE_FILE_LOCAL" <<'PY'
import json, sys, time
f = sys.argv[1]
now = int(time.time())
with open(f, 'w') as fp:
    json.dump({'status':'idle','pid':0,'strategy_hash_before':'','phase':'failed_no_apply','progress':100,'detail':'manual_no_change','started_at':0,'updated_at':now}, fp)
PY
    echo "[manual_improve] strategy.py に変更なし → failed_no_apply として記録"
    echo "[manual_improve] メインループが再開します。"
fi

# ── 以下は後処理 (失敗してもメインループ再開は確定済み) ──
# .env をロード
[ -f .env ] && set -a && . ./.env && set +a

# eloop_lib.sh をsource (後処理用)
if source ./eloop_lib.sh 2>/dev/null; then
    if [ "$hash_before" != "$hash_now" ]; then
        log "[IMPROVE][MANUAL] 戦略更新検出: $hash_before -> $hash_now"

        new_decide_hash=$(python3 extract_decide_hash.py strategy.py 2>/dev/null || echo "")
        prev_decide_hash=""
        [ -f tmp/revert_strategy.py ] && prev_decide_hash=$(python3 extract_decide_hash.py tmp/revert_strategy.py 2>/dev/null || echo "")
        [ -z "$prev_decide_hash" ] && [ -f "$CURRENT_STRATEGY_RUN_FILE" ] && \
            prev_decide_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")

        if [ -n "$new_decide_hash" ]; then
            branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" 2>/dev/null || true)
            [ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
            _reset_current_strategy_run "$new_decide_hash" 2>/dev/null || true
        fi

        _clear_accumulated_data 2>/dev/null || true
        save_strategy_version "manual_improve" 2>/dev/null || true
    else
        log "[IMPROVE][MANUAL] 戦略変更なし"
        date +%s > "$STATE_DIR/last_improve_failed_at" || true
    fi

    log "[IMPROVE][MANUAL] 手動改善完了 → idle"

    # OBS
    ./obs_control.sh hide soren console4 2>/dev/null &

    # soren91 停止 (バックグラウンド)
    if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
        log "[IMPROVE][MANUAL] manual_meriken_mode=on のため、メリケンAI継続"
    elif [ "$(date +%H)" = "20" ]; then
        log "[IMPROVE][MANUAL] 20時台: メリケンAIタイムに移行 → soren91継続"
        soren91_improve 2>/dev/null &
    else
        { soren91_stop 2>/dev/null || true; soren91_improve 2>/dev/null || true; } &
        log "[IMPROVE][MANUAL] soren91停止をバックグラウンドで開始"
    fi
else
    echo "[manual_improve] eloop_lib.sh のsourceに失敗。後処理をスキップ（メインループの再開は完了済み）。"
    # soren91を直接停止
    if [ -d soren91 ]; then
        touch soren91/tmp/stopping 2>/dev/null || true
        touch soren91/tmp/stop 2>/dev/null || true
    fi
fi
