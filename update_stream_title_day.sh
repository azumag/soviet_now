#!/bin/bash
# update_stream_title_day.sh - 配信タイトル内の "day N" を当日の N に更新する。
#
# 「day N」の N を、基準日 (day 1) からの経過日数で毎日算出し、
# Twitch チャンネルタイトルを Helix API (PATCH /helix/channels) で更新する。
# 日付ベースで再計算するため、実行漏れ・二重実行があっても自己修復する (冪等)。
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
#                             (未設定時は TWITCH_PREDICTIONS_TOKEN を流用)
# 任意:
#   STREAM_DAY_EPOCH        : day 1 の日付 (YYYY-MM-DD)。既定 2026-03-14
#   STREAM_DAY_TZ           : 日付判定のタイムゾーン。既定 Asia/Tokyo
#
# 終了コード: 0=更新済/変化なし, 2=タイトルに "day N" が無く更新スキップ,
#            3=トークン/スコープ不足, 4=API エラー, 1=設定不足
cd "$(dirname "$0")"

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
TOKEN="${TWITCH_TITLE_TOKEN:-${TWITCH_PREDICTIONS_TOKEN:-}}"
TOKEN="${TOKEN#oauth:}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$TOKEN" ]; then
	_log "ERROR: no token (set TWITCH_TITLE_TOKEN or TWITCH_PREDICTIONS_TOKEN)"; exit 1
fi
if [ -z "$BROADCASTER_ID" ]; then
	_log "ERROR: TWITCH_BROADCASTER_ID not set"; exit 1
fi

# --- トークン検証: スコープ確認 & client_id 取得 (トークン発行元 client と一致させる) ---
VALIDATE_JSON="$(curl -s -H "Authorization: OAuth ${TOKEN}" https://id.twitch.tv/oauth2/validate)"
read -r TOK_CLIENT_ID HAS_SCOPE <<EOF
$(printf '%s' "$VALIDATE_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('', '0'); raise SystemExit
scopes = d.get('scopes') or []
has = '1' if 'channel:manage:broadcast' in scopes else '0'
print(d.get('client_id') or '', has)
")
EOF
if [ "$HAS_SCOPE" != "1" ]; then
	_log "ERROR: token lacks 'channel:manage:broadcast' scope. タイトル更新には broadcaster の channel:manage:broadcast 付きトークンが必要です。TWITCH_TITLE_TOKEN を設定してください。"
	exit 3
fi
# GET/PATCH の Client-Id はトークン発行元 client と一致する必要がある
EFFECTIVE_CLIENT_ID="${TOK_CLIENT_ID:-$CLIENT_ID}"
if [ -z "$EFFECTIVE_CLIENT_ID" ]; then
	_log "ERROR: no client id (validate returned none and TWITCH_CLIENT_ID unset)"; exit 1
fi

# --- 現在のタイトルを取得 ---
CH_JSON="$(curl -s "https://api.twitch.tv/helix/channels?broadcaster_id=${BROADCASTER_ID}" \
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

# --- "day N" の数値部分のみ置換 (タイトルの他部分は保持) ---
NEW_TITLE="$(python3 - "$CUR_TITLE" "$N" <<'PY'
import sys, re
cur, n = sys.argv[1], sys.argv[2]
# "day 81" / "Day 81" / "DAY  81" のような表記の数値だけを差し替える ("day" の表記は保持)
new, cnt = re.subn(r'(?i)(day\s*)(\d+)', lambda m: m.group(1) + n, cur, count=1)
if cnt == 0:
    print("__NO_DAY_PATTERN__")
else:
    print(new)
PY
)"
if [ "$NEW_TITLE" = "__NO_DAY_PATTERN__" ]; then
	_log "WARN: current title has no 'day N' pattern; skip (タイトルを上書きしません)"
	exit 2
fi

if [ "$MODE" = "show" ]; then
	_log "show only: would set -> $NEW_TITLE"
	echo "current: $CUR_TITLE"
	echo "new    : $NEW_TITLE"
	exit 0
fi

if [ "$NEW_TITLE" = "$CUR_TITLE" ] && [ "$MODE" != "force" ]; then
	_log "already day $N; no change needed"
	exit 0
fi

if [ "$MODE" = "dryrun" ]; then
	_log "dry-run: would PATCH title -> $NEW_TITLE"
	echo "$NEW_TITLE"
	exit 0
fi

# --- タイトル更新 (PATCH /helix/channels) ---
BODY="$(python3 -c "import json,sys; print(json.dumps({'title': sys.argv[1]}))" "$NEW_TITLE")"
HTTP_CODE="$(curl -s -o /tmp/_stream_title_patch_resp.$$ -w '%{http_code}' \
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
