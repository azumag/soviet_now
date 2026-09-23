#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では Twitch クリップ作成を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# twitch_clip.sh - Twitchクリップ自動作成 + チャット投稿
# Usage: ./twitch_clip.sh "イベントメッセージ" [イベント種別]
cd "$(dirname "$0")"
source lib/outbound_queue.sh 2>/dev/null || true

# 単体実行時にも.envを読めるようにする（ループ内ではsoren_loop.shが既にexport済み）
[ -z "${TWITCH_CLIENT_ID:-}" ] && [ -f .env ] && set -a && . ./.env && set +a

EVENT_MSG="${1:-}"
EVENT_KIND="${2:-generic}"
_log() { echo "[twitch_clip $(date '+%H:%M:%S')] $*" >&2; }
# Create Clip は非同期。作成応答だけでは成功にせず、Get Clips で確認する。
# rc=75 は未作成と判明した一時障害、78 は設定/認証、1 は結果不明/未確認。
# rc=1 を自動で再POSTすると同じ建国クリップを重複作成するおそれがある。
CLIP_POLL_MAX="${TWITCH_CLIP_POLL_MAX:-20}"
CLIP_POLL_INTERVAL_SEC="${TWITCH_CLIP_POLL_INTERVAL_SEC:-3}"
CLIP_CONNECT_TIMEOUT_SEC=5
CLIP_REQUEST_TIMEOUT_SEC=10

# --- 環境変数チェック ---
# クリップ作成は TWITCH_CLIP_TOKEN を優先する (clips:edit 付きトークン用。
# 未設定時は従来どおり TWITCH_BOT_TOKEN を使う)。
# 長命ワーカーの継承envが古い場合に備え、.env ファイルからも直接読む。
if [ -z "${TWITCH_CLIP_TOKEN:-}" ] && [ -f .env ]; then
    TWITCH_CLIP_TOKEN=$(grep -a '^TWITCH_CLIP_TOKEN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2-)
fi
TOKEN="${TWITCH_CLIP_TOKEN:-${TWITCH_BOT_TOKEN:-}}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$TOKEN" ] || [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
    _log "SKIP: missing env vars"
    exit 78
fi
TOKEN="${TOKEN#oauth:}"

# --- JSONパーサー（jq不要、python3で統一） ---
_json_get() {
    python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d$1 if d$1 else '')" 2>/dev/null
}

# --- クリップ作成 ---
# HTTPステータスも記録する（offline と scope不足/認証失敗の切り分け用）
clip_http_code=""
response=$(curl -s --connect-timeout "$CLIP_CONNECT_TIMEOUT_SEC" \
    --max-time "$CLIP_REQUEST_TIMEOUT_SEC" -w '\n%{http_code}' -X POST \
    "https://api.twitch.tv/helix/clips?broadcaster_id=${BROADCASTER_ID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Client-Id: ${CLIENT_ID}" 2>/dev/null)
clip_curl_rc=$?
if [ "$clip_curl_rc" -ne 0 ]; then
    _log "WARN: clip create transport failed (rc=$clip_curl_rc)"
    case "$clip_curl_rc" in
        5|6|7) exit 75 ;; # proxy/DNS/connect failure: request was not accepted
        *) exit 1 ;; # timeout/response loss: outcome may be ambiguous
    esac
fi
clip_http_code=$(printf '%s' "$response" | tail -n 1)
response=$(printf '%s' "$response" | sed '$d')
case "$clip_http_code" in
    2*) ;;
    *)
        _log "WARN: clip create failed (http=${clip_http_code:-conn-fail}; offline?/scope clips:edit?/token?)"
        case "$clip_http_code" in
            429|503) exit 75 ;;
            *) exit 78 ;;
        esac
        ;;
esac
if [ -z "$response" ]; then
    _log "WARN: clip create failed (http=${clip_http_code}, empty body)"
    exit 1
fi

clip_id=$(printf '%s' "$response" | _json_get "['data'][0]['id']")
if [ -z "$clip_id" ]; then
    _log "WARN: no clip id in response"
    exit 1
fi
_log "clip created: id=$clip_id"

# --- 完了ポーリング（既定で最大60秒） ---
clip_url=""
poll=1
while [ "$poll" -le "$CLIP_POLL_MAX" ]; do
    sleep "$CLIP_POLL_INTERVAL_SEC"
    clip_info=$(curl -sf --connect-timeout "$CLIP_CONNECT_TIMEOUT_SEC" \
        --max-time "$CLIP_REQUEST_TIMEOUT_SEC" \
        "https://api.twitch.tv/helix/clips?id=${clip_id}" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Client-Id: ${CLIENT_ID}" 2>/dev/null)
    clip_url=$(printf '%s' "$clip_info" | _json_get "['data'][0]['url']")
    if [ -n "$clip_url" ]; then
        _log "clip ready (poll=$poll): $clip_url"
        break
    fi
    _log "clip not ready yet (poll=$poll/$CLIP_POLL_MAX)"
    poll=$((poll + 1))
done

# Get Clips で確認できなかった場合は投稿しない（dead link防止）
if [ -z "$clip_url" ]; then
    _log "WARN: clip not confirmed after polling, skipping chat post"
    exit 1
fi

# --- チャット投稿 ---
chat_msg="${EVENT_MSG:+${EVENT_MSG} | }${clip_url}"
enqueue_chat_message "$chat_msg" "twitch_clip"

# ソ連建国クリップは、Twitch側で公開URLまで確認できた後だけBlueskyへ投稿する。
# ユーザーの !clip やハイスコア等の一般クリップは対象外にする。
if [ "$EVENT_KIND" = "soviet" ]; then
    if [ "${SOVIET_CELEBRATION_BLUESKY_ENABLED:-1}" != "1" ]; then
        _log "Bluesky skip: SOVIET_CELEBRATION_BLUESKY_ENABLED!=1"
    else
        bluesky_output=$(python3 ./tools/bluesky_post.py \
            --text "$EVENT_MSG" \
            --link "$clip_url" \
            --card-title "$EVENT_MSG" \
            --card-description "ソ連建国のTwitchクリップ" \
            --tags "${SOVIET_CELEBRATION_BLUESKY_TAGS:-ソ連建国}" \
            --clip-id "$clip_id" \
            --state-dir "${TMP_STATE_DIR:-tmp/state}/bluesky_clips" 2>&1)
        bluesky_rc=$?
        while IFS= read -r line; do
            [ -n "$line" ] && _log "[BLUESKY] $line"
        done <<<"$bluesky_output"
        case "$bluesky_rc" in
            0) _log "Bluesky clip post finished (clip_id=$clip_id)" ;;
            4) _log "Bluesky skip: credentials are not configured" ;;
            *) _log "WARN: Bluesky clip post failed (rc=$bluesky_rc, clip_id=$clip_id)" ;;
        esac
    fi
fi
_log "done: $clip_url"
