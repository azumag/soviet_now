#!/bin/bash
# say_enqueue.sh - トークンベースのsayキュー（最新が勝つ・プリエンプション付き）
#
# 使い方: ./say_enqueue.sh <content_file> [rate]
#
# 動作:
#   1. コンテンツを登録し、ユニークトークンで「自分の番」を主張
#   2. 前のsayがまだ再生中なら、終了を待つ
#   3. 待っている間に別の呼び出しがトークンを上書きしたら → 諦めて終了
#   4. 前のsay終了後、トークンがまだ自分のものなら → say開始
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

# --- 最終プリエンプションチェック ---
if _is_preempted; then
    _log "最終チェックでプリエンプト → 諦め"
    rm -f "$MY_CONTENT"
    exit 0
fi

# --- say開始（nohupで独立プロセスとして） ---
_log "say開始 (rate=${RATE})"
nohup say -r "$RATE" -f "$MY_CONTENT" > /dev/null 2>&1 &
SAY_PID=$!
echo "$SAY_PID" > "$PID_FILE"

# sayの完了をポーリングで待つ
# このスクリプトが殺されてもsayはnohupで生き残る
while kill -0 "$SAY_PID" 2>/dev/null; do
    sleep 2
done

_log "say終了"
rm -f "$PID_FILE" "$MY_CONTENT"
