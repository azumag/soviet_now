#!/bin/bash
# say_enqueue.sh - mkdirロックベースのsayキュー（最新が勝つ・プリエンプション付き）
#
# 使い方: ./say_enqueue.sh <content_file> [rate]
#
# 動作:
#   1. コンテンツコピー + トークン登録
#   2. mkdirロック取得（待機 + プリエンプション確認）
#   3. ロック内: 前のsay PID待ち + プリエンプション確認
#   4. ロック内: nohup say起動 + PID記録
#   5. ロック解放
#   6. say完了待ち + クリーンアップ

set -uo pipefail
cd "$(dirname "$0")"

QUEUE_DIR="tmp/.say_queue"
mkdir -p "$QUEUE_DIR"

CONTENT_FILE="${1:?Usage: say_enqueue.sh <content_file> [rate]}"
RATE="${2:-120}"

PID_FILE="$QUEUE_DIR/pid"
TOKEN_FILE="$QUEUE_DIR/token"
LOCK_DIR="$QUEUE_DIR/.lock"

if [ ! -s "$CONTENT_FILE" ]; then
    echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
    exit 1
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"

# トークン登録（最後に書いた者が勝つ）
echo "$MY_TOKEN" > "$TOKEN_FILE"

_log() { echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2; }

_is_preempted() {
    [ "$(cat "$TOKEN_FILE" 2>/dev/null)" != "$MY_TOKEN" ]
}

# mkdirロック: アトミックな排他制御（macOS互換）
_acquire_lock() {
    local max_wait=60 waited=0
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        if [ "$waited" -ge "$max_wait" ]; then
            return 1
        fi
        if _is_preempted; then
            return 2
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    return 0
}

_release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null
}

# クリーンアップ: 終了時にロック解放 + 自分のコンテンツ削除
_cleanup() {
    _release_lock
    rm -f "$MY_CONTENT"
}
trap '_cleanup' EXIT

_log "queued (token=${MY_TOKEN})"

# --- mkdirロックで排他制御 ---
_acquire_lock
lock_ret=$?
if [ "$lock_ret" -ne 0 ]; then
    if [ "$lock_ret" -eq 2 ]; then
        _log "ロック待ち中にプリエンプト → 諦め"
    else
        _log "ロック取得タイムアウト → 諦め"
    fi
    exit 0
fi

# --- ロック内: 前のsayが残っていたら待つ ---
if [ -f "$PID_FILE" ]; then
    PREV_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PREV_PID" ] && kill -0 "$PREV_PID" 2>/dev/null; then
        _log "前のsay (PID=$PREV_PID) がまだ再生中 → 終了待ち"
        while kill -0 "$PREV_PID" 2>/dev/null; do
            if _is_preempted; then
                _log "say待ち中にプリエンプト → 諦め"
                exit 0
            fi
            sleep 1
        done
    fi
    rm -f "$PID_FILE"
fi

# --- ロック内: 最終プリエンプションチェック ---
if _is_preempted; then
    _log "最終チェックでプリエンプト → 諦め"
    exit 0
fi

# --- ロック内: say開始 + PID記録（アトミック） ---
_log "say開始 (rate=${RATE})"
nohup say -r "$RATE" -f "$MY_CONTENT" > /dev/null 2>&1 &
SAY_PID=$!
echo "$SAY_PID" > "$PID_FILE"

# ロック解放（say起動+PID記録が完了したので安全）
_release_lock

# sayの完了をポーリングで待つ
# このスクリプトが殺されてもsayはnohupで生き残る
while kill -0 "$SAY_PID" 2>/dev/null; do
    sleep 2
done

_log "say終了"
rm -f "$PID_FILE"
