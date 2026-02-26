#!/bin/bash
# say_enqueue.sh - flockベースのsayキュー（最新が勝つ・プリエンプション付き）
#
# 使い方: ./say_enqueue.sh <content_file> [rate]
#
# 動作:
#   1. コンテンツを登録し、ユニークトークンで「自分の番」を主張
#   2. flockで排他ロックを取得し、前のsay終了を待つ
#   3. ロック取得後、トークンがまだ自分のものなら → say開始
#   4. 別の呼び出しがトークンを上書きしていたら → 諦めて終了
#
# eloop.sh と watch_strategy.sh の両方から呼ばれる。
# sayはnohupで起動するため、このスクリプトが殺されてもsayは生き残る。

set -uo pipefail
cd "$(dirname "$0")"

QUEUE_DIR="tmp/.say_queue"
mkdir -p "$QUEUE_DIR"

CONTENT_FILE="${1:?Usage: say_enqueue.sh <content_file> [rate]}"
RATE="${2:-120}"

PID_FILE="$QUEUE_DIR/pid"
TOKEN_FILE="$QUEUE_DIR/token"
LOCK_FILE="$QUEUE_DIR/lock"

if [ ! -s "$CONTENT_FILE" ]; then
    echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
    exit 1
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"

# 古いコンテンツファイルを掃除（1時間以上前のもの）
find "$QUEUE_DIR" -name 'content_*.txt' -mmin +60 -delete 2>/dev/null

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"

# トークン登録（最後に書いた者が勝つ）
echo "$MY_TOKEN" > "$TOKEN_FILE"

_log() { echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2; }

_is_preempted() {
    [ "$(cat "$TOKEN_FILE" 2>/dev/null)" != "$MY_TOKEN" ]
}

_log "queued (token=${MY_TOKEN})"

# --- 前のsayの終了を待つ（プリエンプションチェック付き） ---
# flockの外でポーリング: ロック取得前にプリエンプトされたら早期終了
while [ -f "$PID_FILE" ]; do
    PREV_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -z "$PREV_PID" ] || ! kill -0 "$PREV_PID" 2>/dev/null; then
        break  # 前のsayは終了済み
    fi
    if _is_preempted; then
        _log "待機中にプリエンプト → 諦め"
        rm -f "$MY_CONTENT"
        exit 0
    fi
    sleep 1
done

# --- flockで排他ロックを取得してからsay開始 ---
# これにより「プリエンプションチェック → say起動 → PID書き込み」がアトミックになる
exec 9>"$LOCK_FILE"
if ! flock -w 30 9; then
    _log "ロック取得タイムアウト → 諦め"
    rm -f "$MY_CONTENT"
    exit 0
fi

# --- ロック内: 前のsayが残っていたら殺す ---
if [ -f "$PID_FILE" ]; then
    PREV_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PREV_PID" ] && kill -0 "$PREV_PID" 2>/dev/null; then
        _log "前のsay (PID=$PREV_PID) がまだ再生中 → 終了を待機"
        # ロック内なので他のプロセスは割り込めない
        while kill -0 "$PREV_PID" 2>/dev/null; do
            sleep 1
        done
    fi
    rm -f "$PID_FILE"
fi

# --- ロック内: 最終プリエンプションチェック ---
if _is_preempted; then
    _log "最終チェックでプリエンプト → 諦め"
    rm -f "$MY_CONTENT"
    flock -u 9
    exit 0
fi

# --- ロック内: say開始 ---
_log "say開始 (rate=${RATE})"
nohup say -r "$RATE" -f "$MY_CONTENT" > /dev/null 2>&1 &
SAY_PID=$!
echo "$SAY_PID" > "$PID_FILE"

# ロック解放（say起動+PID記録が完了したので安全）
flock -u 9

# sayの完了をポーリングで待つ
# このスクリプトが殺されてもsayはnohupで生き残る
while kill -0 "$SAY_PID" 2>/dev/null; do
    sleep 2
done

_log "say終了"
rm -f "$PID_FILE" "$MY_CONTENT"
