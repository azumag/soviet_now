#!/bin/bash
# update_stream_title_day.sh - 配信タイトルのDAYと視聴者向け本文を更新する。
#
# "[dayN]" の N を、基準日 (day 1) からの経過日数で毎日算出し、
# Twitch チャンネルタイトルを Helix API (PATCH /helix/channels) で更新する。
# 日付ベースで再計算するため、実行漏れ・二重実行があっても自己修復する (冪等)。
# 旧形式 "[Game] dayN ..." / "day N" や day 表記が欠落したタイトルも
# "[dayN] ..." に正規化するため、タイトル変更で day カウントが止まらない。
#
# Usage:
#   ./update_stream_title_day.sh            → 当日の N に更新 (変化が無ければ何もしない)
#   ./update_stream_title_day.sh --show     → 現在のタイトルと算出 N を表示するだけ (更新しない)
#   ./update_stream_title_day.sh --dry-run  → 更新後タイトルを表示するだけ (PATCH しない)
#   ./update_stream_title_day.sh --force    → N が同じでも PATCH を実行する
#
# 必要な環境変数 (.env):
#   TWITCH_CLIENT_ID        : Twitch アプリの Client ID
#   TWITCH_BROADCASTER_ID   : チャンネル(broadcaster)の user id
#   TWITCH_TITLE_TOKEN      : channel:manage:broadcast スコープ付きの broadcaster トークン
#                             (既存の予想用・チャット用トークンも権限と本人確認後に利用)
# 任意:
#   STREAM_DAY_EPOCH        : day 1 の日付 (YYYY-MM-DD)。既定 2026-03-14
#   STREAM_DAY_TZ           : 日付判定のタイムゾーン。既定 Asia/Tokyo
#   STREAM_VIEWER_TITLE_FILE : 明示的な視聴者向けタイトル候補。既定 prompts/viewer_title.md
#   STREAM_TITLE_PUBLIC_FALLBACK : 候補も安全な現タイトルも無い場合の一般向け本文
#
# 終了コード: 0=更新済/変化なし, 3=トークン/スコープ不足,
#            4=API エラー, 1=設定不足
cd "$(dirname "$0")"

# Shared with game switches so a daily update cannot race a game/title update.
if command -v flock >/dev/null 2>&1; then
    mkdir -p tmp/state
    exec 9>tmp/state/stream_title_update.lock
    flock -w 60 9 || exit 4
fi

# 毎回の実行で最新の .env を読む (cron/launchd から呼ばれるため)。
[ -f .env ] && set -a && . ./.env && set +a

LOG_FILE="${STREAM_TITLE_LOG_FILE:-tmp/logs/stream_title_day.log}"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
_log() {
	local line="[stream_title $(date '+%Y-%m-%d %H:%M:%S')] $*"
	echo "$line" >&2
	echo "$line" >>"$LOG_FILE" 2>/dev/null || true
}

MODE="update"
case "${1:-}" in
	--show)    MODE="show" ;;
	--dry-run) MODE="dryrun" ;;
	--force)   MODE="force" ;;
	"" )       MODE="update" ;;
	*) _log "ERROR: unknown arg: $1"; exit 1 ;;
esac

EPOCH="${STREAM_DAY_EPOCH:-2026-03-14}"
DAY_TZ="${STREAM_DAY_TZ:-Asia/Tokyo}"

# --- 当日の N を算出 (基準日からの経過日数 + 1) ---
TODAY="$(TZ="$DAY_TZ" date '+%Y-%m-%d')"
N="$(python3 - "$EPOCH" "$TODAY" <<'PY'
import sys, datetime
try:
    e = datetime.date.fromisoformat(sys.argv[1])
    t = datetime.date.fromisoformat(sys.argv[2])
except Exception as ex:
    print("ERR", ex, file=sys.stderr); sys.exit(1)
n = (t - e).days + 1
if n < 1:
    print("ERR epoch in the future", file=sys.stderr); sys.exit(1)
print(n)
PY
)" || { _log "ERROR: failed to compute day N (epoch=$EPOCH today=$TODAY)"; exit 1; }
_log "computed day N=$N (epoch=$EPOCH today=$TODAY tz=$DAY_TZ)"

# --- 認証情報 ---
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
[ -n "$BROADCASTER_ID" ] || { _log "ERROR: TWITCH_BROADCASTER_ID not set"; exit 1; }
TOKEN=""
EFFECTIVE_CLIENT_ID=""
# Select an existing valid broadcaster credential with the required scope.
# A predictions-only credential must not prevent trying the chat credential.
for token_name in TWITCH_TITLE_TOKEN TWITCH_PREDICTIONS_TOKEN TWITCH_BOT_TOKEN; do
    candidate="${!token_name}"
    candidate="${candidate#oauth:}"
    [ -n "$candidate" ] || continue
    VALIDATE_JSON="$(curl -s --max-time 10 -H "Authorization: OAuth ${candidate}" https://id.twitch.tv/oauth2/validate)"
    TOK_CLIENT_ID=$(printf '%s' "$VALIDATE_JSON" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    if str(d.get("user_id", "")) == sys.argv[1] and "channel:manage:broadcast" in d.get("scopes", []):
        print(d.get("client_id", ""))
except (ValueError, TypeError):
    pass
' "$BROADCASTER_ID")
    if [ -n "$TOK_CLIENT_ID" ]; then
        TOKEN="$candidate"
        EFFECTIVE_CLIENT_ID="$TOK_CLIENT_ID"
        _log "using credential: $token_name"
        break
    fi
done
unset candidate VALIDATE_JSON
if [ -z "$TOKEN" ]; then
    _log "ERROR: no valid broadcaster credential with channel:manage:broadcast"
    exit 3
fi

# --- 現在のタイトルを取得 ---
CH_JSON="$(curl -s --max-time 15 "https://api.twitch.tv/helix/channels?broadcaster_id=${BROADCASTER_ID}" \
	-H "Authorization: Bearer ${TOKEN}" -H "Client-Id: ${EFFECTIVE_CLIENT_ID}")"
CUR_TITLE="$(printf '%s' "$CH_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin); it = (d.get('data') or [{}])[0]
    print(it.get('title') or '')
except Exception:
    print('')
")"
if [ -z "$CUR_TITLE" ]; then
	_log "ERROR: failed to fetch current title (resp: $(printf '%s' "$CH_JSON" | head -c 200))"; exit 4
fi
_log "current title: $CUR_TITLE"

# --- [dayN] と視聴者向け本文を更新。内部運用メモは公開タイトルへ流さない。 ---
TITLE_HELPER="${STREAM_TITLE_HELPER:-lib/stream_title_public.py}"
VIEWER_TITLE_FILE="${STREAM_VIEWER_TITLE_FILE:-prompts/viewer_title.md}"
PUBLIC_FALLBACK="${STREAM_TITLE_PUBLIC_FALLBACK:-AIたちがゲーム・ニュース・会話に挑戦する実験配信}"
[ -f "$TITLE_HELPER" ] || { _log "ERROR: public title helper not found: $TITLE_HELPER"; exit 1; }

PUBLIC_BODY="$(python3 "$TITLE_HELPER" choose \
    --current "$CUR_TITLE" \
    --candidate-file "$VIEWER_TITLE_FILE" \
    --fallback "$PUBLIC_FALLBACK")" || {
    _log "ERROR: failed to choose public title body"
    exit 1
}
NEW_TITLE="$(python3 "$TITLE_HELPER" compose \
    --day "$N" \
    --activity "$PUBLIC_BODY" \
    --fallback "$PUBLIC_FALLBACK")" || {
    _log "ERROR: failed to compose public title"
    exit 1
}

if [ "$MODE" = "show" ]; then
	_log "show only: would set -> $NEW_TITLE"
	echo "current: $CUR_TITLE"
	echo "new    : $NEW_TITLE"
	exit 0
fi

if [ "$NEW_TITLE" = "$CUR_TITLE" ] && [ "$MODE" != "force" ]; then
	_log "title already current for day $N; no change needed"
	exit 0
fi

if [ "$MODE" = "dryrun" ]; then
	_log "dry-run: would PATCH title -> $NEW_TITLE"
	echo "$NEW_TITLE"
	exit 0
fi

# --- タイトル更新 (PATCH /helix/channels) ---
BODY="$(python3 -c "import json,sys; print(json.dumps({'title': sys.argv[1]}))" "$NEW_TITLE")"
HTTP_CODE="$(curl -s --max-time 15 -o /tmp/_stream_title_patch_resp.$$ -w '%{http_code}' \
	-X PATCH "https://api.twitch.tv/helix/channels?broadcaster_id=${BROADCASTER_ID}" \
	-H "Authorization: Bearer ${TOKEN}" \
	-H "Client-Id: ${EFFECTIVE_CLIENT_ID}" \
	-H "Content-Type: application/json" \
	-d "$BODY")"
RESP="$(cat /tmp/_stream_title_patch_resp.$$ 2>/dev/null)"; rm -f /tmp/_stream_title_patch_resp.$$ 2>/dev/null
if [ "$HTTP_CODE" = "204" ]; then
	_log "OK: title updated -> $NEW_TITLE"
	exit 0
fi
_log "ERROR: PATCH failed (HTTP $HTTP_CODE): $(printf '%s' "$RESP" | head -c 300)"
exit 4
