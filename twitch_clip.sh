#!/bin/bash
# twitch_clip.sh - Twitchクリップ自動作成 + チャット投稿
# Usage: ./twitch_clip.sh "イベントメッセージ"
cd "$(dirname "$0")"

# 単体実行時にも.envを読めるようにする（ループ内ではsoren_loop.shが既にexport済み）
[ -z "${TWITCH_CLIENT_ID:-}" ] && [ -f .env ] && set -a && . ./.env && set +a

EVENT_MSG="${1:-}"
_log() { echo "[twitch_clip $(date '+%H:%M:%S')] $*" >&2; }

# --- 環境変数チェック ---
TOKEN="${TWITCH_BOT_TOKEN:-}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$TOKEN" ] || [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
    _log "SKIP: missing env vars"
    exit 0
fi
TOKEN="${TOKEN#oauth:}"

# --- JSONパーサー（jq不要、python3で統一） ---
_json_get() {
    python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d$1 if d$1 else '')" 2>/dev/null
}

# --- クリップ作成 ---
response=$(curl -sf -X POST \
    "https://api.twitch.tv/helix/clips?broadcaster_id=${BROADCASTER_ID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Client-Id: ${CLIENT_ID}" 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$response" ]; then
    _log "WARN: clip create failed (stream offline?)"
    exit 0
fi

clip_id=$(printf '%s' "$response" | _json_get "['data'][0]['id']")
if [ -z "$clip_id" ]; then
    _log "WARN: no clip id in response"
    exit 0
fi
_log "clip created: id=$clip_id"

# --- 完了ポーリング（最大15秒） ---
clip_url=""
for i in 1 2 3 4 5; do
    sleep 3
    clip_info=$(curl -sf \
        "https://api.twitch.tv/helix/clips?id=${clip_id}" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Client-Id: ${CLIENT_ID}" 2>/dev/null)
    clip_url=$(printf '%s' "$clip_info" | _json_get "['data'][0]['url']")
    if [ -n "$clip_url" ]; then
        _log "clip ready (poll=$i): $clip_url"
        break
    fi
    _log "clip not ready yet (poll=$i/5)"
done

# Get Clips で確認できなかった場合は投稿しない（dead link防止）
if [ -z "$clip_url" ]; then
    _log "WARN: clip not confirmed after polling, skipping chat post"
    exit 0
fi

# --- チャット投稿 ---
chat_msg="${EVENT_MSG:+${EVENT_MSG} | }${clip_url}"
./twitch_chat.sh send "$chat_msg" >/dev/null 2>&1 || _log "WARN: chat post failed"
_log "done: $clip_url"
