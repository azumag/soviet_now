#!/bin/bash
# update_stream_game.sh - ゲーム切替時に Twitch の配信カテゴリーと配信タイトルを切り替える。
#
# 背景: ゲーム切替 (docich switch / lifecycle broker) が別プロセスで整備中のため、
# 自動フックは未接続。切替が一段落するまでは切替時に手動で本スクリプトを実行する。
#   ./update_stream_game.sh --game robots --strategy "v wholesale..."
#
# 配信タイトルは handoff 由来 (prompts/ops_brief.md の1件目=直近の作業) を既定の
# activity とし、ゲーム・戦略の進捗を --strategy で乗せる。既存の "day N" 維持のうえ
# 先頭に [twitch].title_prefix を付ける (例: "[Robots] day176 ...")。
# カテゴリーは Twitch (IGDB) の game_id を Helix PATCH /helix/channels で更新する。
# game_id の選定は --resolve/--verify で IGDB 照合する (docs/twitch_game_sync.md)。
#
# Usage:
#   ./update_stream_game.sh --game <id> [--strategy TEXT] [--activity TEXT]
#   ./update_stream_game.sh --game <id> --dry-run
#   ./update_stream_game.sh --game <id> --show
#   ./update_stream_game.sh --game <id> --title-only [--strategy TEXT]
#   ./update_stream_game.sh --verify --game <id>   → toml と Twitch 実登録の照合のみ
#   ./update_stream_game.sh --resolve "query"      → IGDB カテゴリー候補の表示のみ
#
# ゲーム定義: docich config/games/<id>.toml の [twitch] テーブル。
#   [twitch]
#   category_id = "11585"
#   category_name = "Robots"      # Twitch上の正式名 (verify用)
#   title_prefix = "[Robots]"     # 配信タイトル先頭
# --toml PATH / --category-id ID などで上書き・単独指定も可。
#
# 必要な環境変数 (.env):
#   TWITCH_CLIENT_ID / TWITCH_BROADCASTER_ID (既存と共通)
#   トークン (優先順): TWITCH_GAME_TOKEN > TWITCH_TITLE_TOKEN >
#     TWITCH_BOT_TOKEN > TWITCH_PREDICTIONS_TOKEN
#   いずれも channel:manage:broadcast スコープ必須 (PATCH用)。
#   (実測: PREDICTIONS は polls/predictions のみ、旧 TITLE は失効、
#    BOT は broadcast 付き。--verify/--resolve は読取のみで scope 不要)
# 任意:
#   STREAM_DAY_EPOCH (既定 2026-03-14), STREAM_DAY_TZ (既定 Asia/Tokyo)
#   OPS_BRIEF_FILE (既定 prompts/ops_brief.md), STREAM_GAME_STRATEGY
#
# 終了コード: 0=更新済/変化なし, 1=設定・使い方エラー, 3=トークン/スコープ不足,
#            4=API エラー, 5=verify 不一致
cd "$(dirname "$0")"

# 毎回の実行で最新の .env を読む (cron/手動から呼ばれるため)。
[ -f .env ] && set -a && . ./.env && set +a

LOG_FILE="${STREAM_GAME_LOG_FILE:-tmp/logs/stream_game.log}"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
_log() {
	local line="[stream_game $(date '+%Y-%m-%d %H:%M:%S')] $*"
	echo "$line" >&2
	echo "$line" >>"$LOG_FILE" 2>/dev/null || true
}

MODE="update"
GAME="" TOML="" GAMES_DIR="" CAT_ID_ARG="" CAT_NAME_ARG="" PREFIX_ARG=""
TITLE_ONLY=0 RESOLVE_QUERY=""
while [ "$#" -gt 0 ]; do
	case "$1" in
		--game) GAME="$2"; shift 2 ;;
		--toml) TOML="$2"; shift 2 ;;
		--games-dir) GAMES_DIR="$2"; shift 2 ;;
		--category-id) CAT_ID_ARG="$2"; shift 2 ;;
		--category-name) CAT_NAME_ARG="$2"; shift 2 ;;
		--title-prefix) PREFIX_ARG="$2"; shift 2 ;;
		--activity) ACTIVITY_ARG="$2"; shift 2 ;;
		--strategy) STRATEGY_ARG="$2"; shift 2 ;;
		--day) DAY_OVERRIDE="$2"; shift 2 ;;
		--title-only) TITLE_ONLY=1; shift ;;
		--show) MODE="show"; shift ;;
		--dry-run) MODE="dryrun"; shift ;;
		--dryrun) MODE="dryrun"; shift ;;
		--force) MODE="force"; shift ;;
		--verify) MODE="verify"; shift ;;
		--resolve) RESOLVE_QUERY="$2"; MODE="resolve"; shift 2 ;;
		--help|-h) sed -n '1,40p' "$0"; exit 0 ;;
		*) _log "ERROR: unknown arg: $1"; exit 1 ;;
	esac
done

EPOCH="${STREAM_DAY_EPOCH:-2026-03-14}"
DAY_TZ="${STREAM_DAY_TZ:-Asia/Tokyo}"

# --- 認証情報 (読取系でも Client-Id 解決に使う) ---
TOKEN="${TWITCH_GAME_TOKEN:-${TWITCH_TITLE_TOKEN:-${TWITCH_BOT_TOKEN:-${TWITCH_PREDICTIONS_TOKEN:-}}}}"
TOKEN="${TOKEN#oauth:}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$TOKEN" ]; then
	_log "ERROR: no token (set TWITCH_GAME_TOKEN, TWITCH_TITLE_TOKEN, TWITCH_BOT_TOKEN or TWITCH_PREDICTIONS_TOKEN)"; exit 1
fi
if [ -z "$BROADCASTER_ID" ] && [ "$MODE" != "resolve" ]; then
	_log "ERROR: TWITCH_BROADCASTER_ID not set"; exit 1
fi

# --- トークン検証: スコープ確認 & client_id 取得 ---
VALIDATE_JSON="$(curl -s -H "Authorization: OAuth ${TOKEN}" https://id.twitch.tv/oauth2/validate)"
read -r TOK_CLIENT_ID HAS_SCOPE TOK_LOGIN <<EOF
$(printf '%s' "$VALIDATE_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('', '0', ''); raise SystemExit
scopes = d.get('scopes') or []
has = '1' if 'channel:manage:broadcast' in scopes else '0'
print(d.get('client_id') or '', has, d.get('login') or '')
")
EOF
EFFECTIVE_CLIENT_ID="${TOK_CLIENT_ID:-$CLIENT_ID}"
if [ -z "$EFFECTIVE_CLIENT_ID" ]; then
	_log "ERROR: no client id (token invalid? resp: $(printf '%s' "$VALIDATE_JSON" | head -c 120))"; exit 3
fi
if [ "$HAS_SCOPE" != "1" ] && [ "$MODE" != "verify" ] && [ "$MODE" != "resolve" ]; then
	_log "ERROR: token(login=${TOK_LOGIN:-?}) lacks 'channel:manage:broadcast' scope. TWITCH_GAME_TOKEN (broadcaster の broadcast 付きトークン) を設定してください。"
	exit 3
fi

_twitch_get() {
	curl -s "$1" -H "Authorization: Bearer ${TOKEN}" -H "Client-Id: ${EFFECTIVE_CLIENT_ID}"
}

# --- --resolve: IGDB カテゴリー候補の表示のみ (書込なし) ---
if [ "$MODE" = "resolve" ]; then
	QENC="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$RESOLVE_QUERY")"
	_twitch_get "https://api.twitch.tv/helix/search/categories?query=${QENC}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'ERROR: bad response: {e}', file=sys.stderr); raise SystemExit(4)
rows = d.get('data', [])[:10]
if not rows:
    print('(no candidates)')
for x in rows:
    print(f\"{x.get('id')} | {x.get('name')}\")
"
	exit "$?"
fi

# --- ゲーム定義 [twitch] の解決 ---
if [ -z "$TOML" ] && [ -n "$GAME" ]; then
	if [ -z "$GAMES_DIR" ]; then
		# soviet_now submodule 内 (docich/games/soviet_now) → ../../config/games、
		# それ以外は ./config/games を既定にする。
		if [ -f "../../config/games/${GAME}.toml" ]; then
			GAMES_DIR="../../config/games"
		else
			GAMES_DIR="./config/games"
		fi
	fi
	TOML="${GAMES_DIR}/${GAME}.toml"
fi
if [ -z "$CAT_ID_ARG" ] && [ -z "$TOML" ]; then
	_log "ERROR: --game <id> (または --toml/--category-id) が必要です"; exit 1
fi
if [ -n "$TOML" ] && [ -z "$CAT_ID_ARG" ] && [ ! -f "$TOML" ]; then
	_log "ERROR: game toml not found: $TOML"; exit 1
fi

IFS=$'\t' read -r CAT_ID CAT_NAME PREFIX <<EOF
$(python3 - "$TOML" "$CAT_ID_ARG" "$CAT_NAME_ARG" "$PREFIX_ARG" <<'PY'
import sys, tomllib
toml_path, id_arg, name_arg, prefix_arg = sys.argv[1:5]
cat_id, cat_name, prefix = id_arg, name_arg, prefix_arg
if not cat_id and toml_path and toml_path != "":
    try:
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as e:
        print(f"ERR load {toml_path}: {e}", file=sys.stderr); raise SystemExit(1)
    tw = data.get("twitch", {})
    if not isinstance(tw, dict):
        print("ERR [twitch] is not a table", file=sys.stderr); raise SystemExit(1)
    cat_id = str(tw.get("category_id") or "")
    if not name_arg:
        cat_name = str(tw.get("category_name") or "")
    if not prefix_arg:
        prefix = str(tw.get("title_prefix") or "")
if cat_id and not cat_id.isdigit():
    print(f"ERR category_id must be numeric: {cat_id!r}", file=sys.stderr); raise SystemExit(1)
print(f"{cat_id}\t{cat_name}\t{prefix}")
PY
)
EOF
if [ -z "$CAT_ID" ]; then
	_log "ERROR: [twitch].category_id が解決できません (toml=$TOML)。docs/twitch_game_sync.md の手順で IGDB 照合して設定してください。"
	exit 1
fi
if [ "$TITLE_ONLY" != "1" ]; then
	_log "target game=${GAME:-?} category_id=$CAT_ID category_name=${CAT_NAME:-?} prefix=${PREFIX:-?}"
fi

# --- --verify: toml と Twitch 実登録の照合のみ (書込なし) ---
if [ "$MODE" = "verify" ]; then
	GAMES_JSON="$(_twitch_get "https://api.twitch.tv/helix/games?id=${CAT_ID}")"
	printf '%s' "$GAMES_JSON" | python3 -c "
import sys, json
want_id, want_name = sys.argv[1], sys.argv[2]
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'ERROR: bad response: {e}', file=sys.stderr); raise SystemExit(4)
rows = d.get('data', [])
if not rows:
    print(f'MISMATCH: id {want_id} not found on Twitch', file=sys.stderr); raise SystemExit(5)
got = rows[0].get('name') or ''
print(f\"twitch registered: id={rows[0].get('id')} name={got!r}\")
if want_name and got != want_name:
    print(f'MISMATCH: toml category_name={want_name!r} != twitch {got!r}', file=sys.stderr); raise SystemExit(5)
print('OK: match')
" "$CAT_ID" "$CAT_NAME"
	exit "$?"
fi

# --- 当日の N を算出 ---
if [ -n "${DAY_OVERRIDE:-}" ]; then
	N="$DAY_OVERRIDE"
else
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
fi

# --- activity 既定: handoff 由来の ops_brief 1件目 ---
if [ -z "${ACTIVITY_ARG+x}" ]; then
	OPS_BRIEF="${OPS_BRIEF_FILE:-prompts/ops_brief.md}"
	ACTIVITY="$(python3 - "$OPS_BRIEF" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("- ") and len(s) > 2:
            print(s[2:].strip()); break
except FileNotFoundError:
    pass
PY
)"
	if [ -z "$ACTIVITY" ]; then
		_log "WARN: ops_brief から activity を取れません ($OPS_BRIEF)。--activity で明示できます"
	fi
else
	ACTIVITY="$ACTIVITY_ARG"
fi
STRATEGY="${STRATEGY_ARG:-${STREAM_GAME_STRATEGY:-}}"

# --- 目標タイトルを組成 (Twitch 上限 140字。prefix+day を優先保持) ---
NEW_TITLE="$(python3 - "$PREFIX" "$N" "$ACTIVITY" "$STRATEGY" <<'PY'
import sys
prefix, n, activity, strategy = (a.strip() for a in sys.argv[1:5])
parts = []
if prefix:
    parts.append(prefix)
parts.append(f"day{n}")
def short(s, limit):
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:max(0, limit - 1)].rstrip() + "…"
if strategy:
    strategy = short(strategy, 40)
if activity:
    # 全体 140字に収める: strategy(あれば) を守り activity を削る
    rest = 140 - len(" ".join(parts)) - (1 + len(strategy) if strategy else 0) - 1
    activity = short(activity, max(0, rest))
    if activity:
        parts.append(activity)
if strategy:
    # 再計算 (activity 短縮後に入り直す)
    rest = 140 - len(" ".join(parts)) - 1
    if rest >= 4:
        parts.append(short(strategy, rest))
    elif not activity:
        parts.append(short(strategy, 140 - len(" ".join(parts)) - 1))
title = " ".join(parts)
print(title[:140])
PY
)"
_log "desired title: $NEW_TITLE"

# --- 現在の title/game を取得 ---
CH_JSON="$(_twitch_get "https://api.twitch.tv/helix/channels?broadcaster_id=${BROADCASTER_ID}")"
CH_OUT="$(printf '%s' "$CH_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin); it = (d.get('data') or [{}])[0]
    t = (it.get('title') or '').replace(chr(10), ' ')
    print(t); print(it.get('game_id') or ''); print(it.get('game_name') or '')
except Exception:
    print(''); print(''); print('')
")"
CUR_TITLE="$(printf '%s' "$CH_OUT" | sed -n '1p')"
CUR_GAME_ID="$(printf '%s' "$CH_OUT" | sed -n '2p')"
CUR_GAME_NAME="$(printf '%s' "$CH_OUT" | sed -n '3p')"
if [ -z "$CUR_TITLE" ]; then
	_log "ERROR: failed to fetch current channel (resp: $(printf '%s' "$CH_JSON" | head -c 200))"; exit 4
fi
_log "current: game_id=$CUR_GAME_ID game_name=${CUR_GAME_NAME:-?} title=$CUR_TITLE"

if [ "$MODE" = "show" ]; then
	echo "current game : ${CUR_GAME_ID} ${CUR_GAME_NAME:-?}"
	echo "current title: $CUR_TITLE"
	echo "new game     : $CAT_ID ${CAT_NAME:-?}"
	echo "new title    : $NEW_TITLE"
	exit 0
fi

WANT_GAME_ID="$CAT_ID"
if [ "$TITLE_ONLY" = "1" ]; then
	WANT_GAME_ID="$CUR_GAME_ID"
fi
if [ "$NEW_TITLE" = "$CUR_TITLE" ] && [ "$WANT_GAME_ID" = "$CUR_GAME_ID" ] && [ "$MODE" != "force" ]; then
	_log "already up to date; no change needed"
	exit 0
fi

if [ "$MODE" = "dryrun" ]; then
	_log "dry-run: would PATCH title=$NEW_TITLE game_id=$WANT_GAME_ID"
	echo "$NEW_TITLE"
	exit 0
fi

# --- 更新 (PATCH /helix/channels: title + game_id を1回で) ---
BODY="$(python3 -c "import json,sys; print(json.dumps({'title': sys.argv[1], 'game_id': sys.argv[2]}))" "$NEW_TITLE" "$WANT_GAME_ID")"
HTTP_CODE="$(curl -s -o /tmp/_stream_game_patch_resp.$$ -w '%{http_code}' \
	-X PATCH "https://api.twitch.tv/helix/channels?broadcaster_id=${BROADCASTER_ID}" \
	-H "Authorization: Bearer ${TOKEN}" \
	-H "Client-Id: ${EFFECTIVE_CLIENT_ID}" \
	-H "Content-Type: application/json" \
	-d "$BODY")"
RESP="$(cat /tmp/_stream_game_patch_resp.$$ 2>/dev/null)"; rm -f /tmp/_stream_game_patch_resp.$$ 2>/dev/null
if [ "$HTTP_CODE" = "204" ]; then
	_log "OK: updated title=$NEW_TITLE game_id=$WANT_GAME_ID"
	exit 0
fi
_log "ERROR: PATCH failed (HTTP $HTTP_CODE): $(printf '%s' "$RESP" | head -c 300)"
exit 4
