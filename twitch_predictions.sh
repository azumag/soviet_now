#!/bin/bash
# twitch_predictions.sh - Twitch チャネルポイント予想 (Predictions) API wrapper
# Usage:
#   ./twitch_predictions.sh create              → prediction_id と outcome_ids を stdout に JSON 出力
#   ./twitch_predictions.sh resolve <outcome_index>  → 勝者 outcome で解決 (0-3)
#   ./twitch_predictions.sh cancel              → キャンセル
#   ./twitch_predictions.sh status              → リモート予想の状態を秘密情報なしで JSON 出力
#   ./twitch_predictions.sh sync                → リモート ACTIVE/LOCKED 予想をローカルへ同期
#
# 状態ファイル: tmp/state/current_prediction.json
cd "$(dirname "$0")"
source lib/outbound_queue.sh 2>/dev/null || true

PREDICTIONS_COMMAND="${1:-}"

# 予想系は毎回の実行で最新の .env を読む。
# 長寿命の親プロセスから古い exported env を引き継いでも、
# 後から追加・更新した Twitch 関連トークンをここで反映させる。
[ -f .env ] && set -a && . ./.env && set +a

_log() { echo "[twitch_predictions $(date '+%H:%M:%S')] $*" >&2; }

TMP_STATE_DIR="${TMP_STATE_DIR:-tmp/state}"

# --- 環境変数チェック ---
if [ "${TWITCH_PREDICTIONS_ENABLED:-0}" != "1" ]; then
	if [ "$PREDICTIONS_COMMAND" = "status" ]; then
		printf '%s\n' '{"ok":true,"enabled":false,"configured":false,"explore_mode":false,"http_code":null,"data":[],"error":"disabled"}'
		exit 0
	fi
	_log "SKIP: TWITCH_PREDICTIONS_ENABLED is not 1"
	exit 0
fi

# 予想はチャネルオーナーのトークンが必須 (TWITCH_PREDICTIONS_TOKEN)
# チャット投稿用の TWITCH_BOT_TOKEN とは別
TOKEN="${TWITCH_PREDICTIONS_TOKEN:-}"
if [ -z "$TOKEN" ]; then
	if [ "$PREDICTIONS_COMMAND" = "status" ]; then
		printf '%s\n' '{"ok":false,"enabled":true,"configured":false,"explore_mode":false,"http_code":null,"data":[],"error":"missing_token"}'
		exit 0
	fi
	_log "SKIP: TWITCH_PREDICTIONS_TOKEN not set"
	exit 0
fi
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
	if [ "$PREDICTIONS_COMMAND" = "status" ]; then
		printf '%s\n' '{"ok":false,"enabled":true,"configured":false,"explore_mode":false,"http_code":null,"data":[],"error":"missing_client_or_broadcaster"}'
		exit 0
	fi
	_log "SKIP: missing env vars (TWITCH_CLIENT_ID, TWITCH_BROADCASTER_ID)"
	exit 0
fi
TOKEN="${TOKEN#oauth:}"

# 探索モード (EXPLORE_MODE=1) では Twitch 予想を操作しない。ただし status は
# 管理画面で現在状態を確認できるように API へ到達させる。
EXPLORE_MODE_ACTIVE="false"
if [ "${EXPLORE_MODE:-0}" = "1" ]; then
	EXPLORE_MODE_ACTIVE="true"
	if [ "$PREDICTIONS_COMMAND" != "status" ]; then
		exit 0
	fi
fi

PREDICTION_STATE_FILE="tmp/state/current_prediction.json"
# MIN_GAMES_BEFORE_IMPROVE 解決: 環境変数 (soren_loop の hot-reload export) を
# 最優先、次に .env のリテラル、次に config.sh、最後に 12。
# set_toggle.sh によるサイクル変更に予想チャットも追従させる。
_cfg_min_games=$(sed -n 's/^[[:space:]]*MIN_GAMES_BEFORE_IMPROVE=\([0-9]*\).*/\1/p' .env 2>/dev/null | tail -1)
[ -z "$_cfg_min_games" ] && _cfg_min_games=$(sed -n 's/^MIN_GAMES_BEFORE_IMPROVE=\([0-9]*\).*/\1/p' core/config.sh 2>/dev/null)
_cfg_min_games="${MIN_GAMES_BEFORE_IMPROVE:-${_cfg_min_games:-12}}"
PREDICTION_MAX_GAMES="${TWITCH_PREDICTION_MAX_GAMES:-$_cfg_min_games}"
# 投票受付時間: 1試合あたり40秒 × サイクル試合数 (base: 12*40=480秒=8分)
# Twitch Predictions API の上限は1800秒。改善サイクルが45試合を超える場合も、
# 受付時間だけはAPIの上限内へ丸め、予想の解決stateはサイクル完了まで保持する。
PREDICTION_WINDOW_MAX_SEC=1800
_prediction_window_default=$((_cfg_min_games * 40))
PREDICTION_WINDOW_SEC="${TWITCH_PREDICTION_WINDOW_SEC:-$_prediction_window_default}"
case "$PREDICTION_WINDOW_SEC" in
''|*[!0-9]*) PREDICTION_WINDOW_SEC="$_prediction_window_default" ;;
esac
[ "$PREDICTION_WINDOW_SEC" -ge 1 ] 2>/dev/null || PREDICTION_WINDOW_SEC=1
[ "$PREDICTION_WINDOW_SEC" -le "$PREDICTION_WINDOW_MAX_SEC" ] 2>/dev/null || PREDICTION_WINDOW_SEC="$PREDICTION_WINDOW_MAX_SEC"
# 投票受付時間を過ぎても、サイクル完了までは resolve 用 state を保持する。
# 必要なら明示的に max age を設定して最終的な掃除だけ行う。
PREDICTION_STATE_MAX_AGE_SEC="${TWITCH_PREDICTION_STATE_MAX_AGE_SEC:-0}"
AUTO_VOTE_POINTS="${TWITCH_AUTO_VOTE_POINTS:-10}"
PREDICTION_RETRY_DIR="${TMP_STATE_DIR}/prediction_retry"
PREDICTION_CREATE_RETRY_FILE="$PREDICTION_RETRY_DIR/create.json"
PREDICTION_RESOLVE_RETRY_FILE="$PREDICTION_RETRY_DIR/resolve.json"
mkdir -p "$PREDICTION_RETRY_DIR" 2>/dev/null || true

_prediction_retry_file() {
	local operation="$1"
	case "$operation" in
	create) printf '%s\n' "$PREDICTION_CREATE_RETRY_FILE" ;;
	resolve) printf '%s\n' "$PREDICTION_RESOLVE_RETRY_FILE" ;;
	*) return 1 ;;
	esac
}

_prediction_retry_remaining() {
	local operation="$1" file now next
	file=$(_prediction_retry_file "$operation") || { echo 0; return; }
	[ -f "$file" ] || { echo 0; return; }
	now=$(date +%s)
	next=$(python3 - "$file" <<'PY' 2>/dev/null
import json, sys
try:
    print(int((json.load(open(sys.argv[1], encoding="utf-8")) or {}).get("next_retry_at", 0) or 0))
except Exception:
    print(0)
PY
)
	case "$next" in ''|*[!0-9]*) next=0 ;; esac
	if [ "$next" -le "$now" ]; then
		rm -f "$file" 2>/dev/null || true
		echo 0
	else
		echo $((next - now))
	fi
}

_prediction_retry_active() {
	local remaining
	remaining=$(_prediction_retry_remaining "$1")
	case "$remaining" in ''|*[!0-9]*) return 1 ;; esac
	[ "$remaining" -gt 0 ]
}

_prediction_retry_record() {
	local operation="$1" http_code="${2:-000}" message="${3:-unknown}" file attempt base max delay now
	file=$(_prediction_retry_file "$operation") || return 0
	base="${PREDICTION_RETRY_BASE_SEC:-300}"
	max="${PREDICTION_RETRY_MAX_SEC:-3600}"
	case "$base" in ''|*[!0-9]*) base=300 ;; esac
	case "$max" in ''|*[!0-9]*) max=3600 ;; esac
	[ "$max" -ge "$base" ] || max="$base"
	attempt=$(python3 - "$file" <<'PY' 2>/dev/null
import json, sys
try:
    print(int((json.load(open(sys.argv[1], encoding="utf-8")) or {}).get("attempt", 0) or 0) + 1)
except Exception:
    print(1)
PY
)
	case "$attempt" in ''|*[!0-9]*) attempt=1 ;; esac
	[ "$attempt" -le 6 ] || attempt=6
	delay=$((base * (1 << (attempt - 1))))
	case "$http_code" in
	401|403) [ "$delay" -lt 900 ] && delay=900 ;;
	esac
	[ "$delay" -le "$max" ] || delay="$max"
	now=$(date +%s)
	python3 - "$file" "$operation" "$attempt" "$((now + delay))" "$http_code" "$message" <<'PY' 2>/dev/null || true
import json, os, sys
path, operation, attempt, next_retry, http_code, message = sys.argv[1:7]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump({"operation": operation, "attempt": int(attempt),
               "next_retry_at": int(next_retry), "http_code": http_code,
               "message": message[:240]}, handle, ensure_ascii=False)
os.replace(tmp, path)
PY
	printf '%s\n' "$delay"
}

_prediction_retry_clear() {
	local file
	file=$(_prediction_retry_file "$1") || return 0
	rm -f "$file" 2>/dev/null || true
}

_prediction_api_error_message() {
	python3 - "${1:-}" <<'PY' 2>/dev/null
import json, sys
try:
    raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    data = json.loads(raw)
    print(str(data.get("message") or data.get("error") or "unknown")[:240])
except Exception:
    print("unknown")
PY
}

_recover_remote_active_prediction() {
	local response_file response_code remote_json prediction_id
	response_file=$(mktemp "$PREDICTION_RETRY_DIR/remote.XXXXXX" 2>/dev/null || echo "$PREDICTION_RETRY_DIR/remote.$$")
	response_code=$(curl -sS --max-time 15 -o "$response_file" -w '%{http_code}' -G \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		--data-urlencode "broadcaster_id=${BROADCASTER_ID}" \
		--data-urlencode "first=20" 2>/dev/null || echo 000)
	if [ "$response_code" != "200" ]; then
		rm -f "$response_file"
		return 1
	fi
	remote_json=$(cat "$response_file" 2>/dev/null || true)
	rm -f "$response_file"
	prediction_id=$(python3 - "$remote_json" <<'PY' 2>/dev/null
import json, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
for pred in data.get("data", []):
    if pred.get("status") not in {"ACTIVE", "LOCKED"}:
        continue
    outcomes = [str(item.get("id", "")) for item in pred.get("outcomes", []) if item.get("id")]
    if len(outcomes) < 2 or not pred.get("id"):
        continue
    print(json.dumps({"prediction_id": pred["id"], "outcome_ids": outcomes,
                      "game_num": 0, "created_at": 0, "recovered": True}, ensure_ascii=False))
    break
PY
)
	[ -n "$prediction_id" ] || return 1
	mkdir -p "$(dirname "$PREDICTION_STATE_FILE")"
	printf '%s\n' "$prediction_id" >"$PREDICTION_STATE_FILE"
	_log "RECOVER: adopted active remote prediction"
	return 0
}

_prediction_state_stale_reason() {
	[ -f "$PREDICTION_STATE_FILE" ] || return 1
	local acc_file="${ACCUMULATED_GAMES_FILE:-tmp/state/accumulated_games.json}"
	python3 - "$PREDICTION_STATE_FILE" "$acc_file" "$PREDICTION_MAX_GAMES" "$PREDICTION_STATE_MAX_AGE_SEC" <<'PY' 2>/dev/null
import json
import sys
import time

state_file, acc_file, max_games_raw, max_age_raw = sys.argv[1:5]
max_games = int(max_games_raw or 0)
max_age_sec = int(max_age_raw or 0)

try:
    state = json.load(open(state_file))
except Exception:
    print("invalid_json")
    raise SystemExit(0)

prediction_id = str(state.get("prediction_id", "") or "")
outcome_ids = state.get("outcome_ids", []) or []
created_at = int(state.get("created_at", 0) or 0)
reasons = []

if not prediction_id or len(outcome_ids) < 2:
    reasons.append("invalid_state")

if created_at > 0:
    age = max(0, int(time.time()) - created_at)
    if max_age_sec > 0 and age >= max_age_sec:
        reasons.append(f"age={age}s")

# サイクル蓄積数 (acc_count) ベースで判定
if max_games > 0:
    try:
        acc = json.load(open(acc_file))
        acc_count = int(acc.get("count", 0) or 0)
    except Exception:
        acc_count = 0
    if acc_count >= max_games:
        reasons.append(f"acc_count={acc_count}")

if reasons:
    print(",".join(reasons))
PY
}

_resolve_prediction_with_best_outcome() {
	# stale な予想を best_outcome で resolve する (未記録なら「建国なし」= index 0)
	[ -f "$PREDICTION_STATE_FILE" ] || return 1
	local state prediction_id winning_outcome_id payload best_outcome
	state=$(cat "$PREDICTION_STATE_FILE")
	prediction_id=$(echo "$state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prediction_id"])' 2>/dev/null)
	# best_outcome をサイクル中の記録から読む (eloop.sh:324 で更新される)
	# ロシア建国フラグも考慮：russia_created=trueなら最低1（ロシア建国）
	best_outcome=$(python3 -c "
import json, sys
try:
    d = json.load(open('$TMP_STATE_DIR/current_prediction.json'))
    outcome = d.get('best_outcome', 0)
    # ロシア建国したがまだベストが更新されていない場合、ロシア建国を設定
    if d.get('russia_created', False) and outcome < 1:
        outcome = 1
    print(outcome)
except Exception:
    print(0)
" 2>/dev/null || echo 0)
	winning_outcome_id=$(echo "$state" | python3 -c "import json,sys; d=json.load(sys.stdin); idx=min(int('$best_outcome'),len(d['outcome_ids'])-1); print(d['outcome_ids'][idx])" 2>/dev/null)
	[ -n "$prediction_id" ] && [ -n "$winning_outcome_id" ] || return 1

	payload=$(python3 -c "
import json, sys
print(json.dumps({
    'broadcaster_id': '$BROADCASTER_ID',
    'id': '$prediction_id',
    'status': 'RESOLVED',
    'winning_outcome_id': '$winning_outcome_id'
}))
" 2>/dev/null)

	local response_file response_code api_message
	response_file=$(mktemp "$PREDICTION_RETRY_DIR/resolve.XXXXXX" 2>/dev/null || echo "$PREDICTION_RETRY_DIR/resolve.$$")
	response_code=$(curl -sS --max-time 15 -o "$response_file" -w '%{http_code}' -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" 2>/dev/null || echo 000)
	if [ "$response_code" -lt 200 ] 2>/dev/null || [ "$response_code" -ge 300 ] 2>/dev/null; then
		api_message=$(_prediction_api_error_message "$response_file")
		_prediction_retry_record resolve "$response_code" "$api_message" >/dev/null
		_log "WARN: stale prediction resolve failed (http=${response_code:-000}, message=${api_message:-unknown})"
		rm -f "$response_file"
		return 1
	fi
	rm -f "$response_file"
	_prediction_retry_clear resolve
	return 0
}

_clear_stale_prediction_state_if_any() {
	local stale_reason=""
	stale_reason=$(_prediction_state_stale_reason || true)
	[ -n "$stale_reason" ] || return 1
	_log "STALE: resolving prediction with best_outcome before clearing (${stale_reason})"
	if _resolve_prediction_with_best_outcome; then
		local _stale_labels=("建国なし" "ロシア建国(ソ連不成立)" "ソ連建国" "粛清")
		local _stale_best
		_stale_best=$(python3 -c "
import json
try:
    d = json.load(open('$TMP_STATE_DIR/current_prediction.json'))
    o = d.get('best_outcome', 0)
    if d.get('russia_created', False) and o < 1: o = 1
    print(o)
except Exception: print(0)
" 2>/dev/null || echo 0)
		local _stale_label="${_stale_labels[$_stale_best]:-index=$_stale_best}"
		_log "STALE: prediction resolved on Twitch: $_stale_label"
		if [ "${_stale_best}" = "3" ]; then
			local _stale_regression_detail=""
			_stale_regression_detail=$(python3 - "$TMP_STATE_DIR/current_prediction.json" 2>/dev/null <<'PY'
import json, sys
f = sys.argv[1]
try:
    d = json.load(open(f))
    # 視聴者向けに整形済みのラベルのみ使う(生コードは出さない)
    label = d.get("regression_reason_label", "")
    if label:
        print(f"理由：{label}")
except Exception:
    pass
PY
)
			[ -n "${_stale_regression_detail}" ] || _stale_regression_detail=""
			if [ -n "${_stale_regression_detail}" ]; then
				enqueue_chat_message "予想結果：「${_stale_label}」！試していた新戦略が前より成績を落としたので、安定版に戻しました。${_stale_regression_detail}" "predictions"
			else
				enqueue_chat_message "予想結果：「${_stale_label}」！試していた新戦略が前より成績を落としたので、安定版に戻しました。" "predictions"
			fi
		else
			enqueue_chat_message "予想結果：「${_stale_label}」でした！" "predictions"
		fi
	else
		_log "STALE: resolve failed, prediction may need manual cleanup"
		# 認証失敗や一時的なAPI障害で remote の状態を確認できない場合は
		# ローカル state を消さない。消すと Twitch 側に ACTIVE/LOCKED が
		# 残ったまま新しい予想を作り、二重予想や未解決を招く。
		return 1
	fi
	rm -f "$PREDICTION_STATE_FILE"
	return 0
}

_load_prediction_state_json() {
	local state_file="${1:-$PREDICTION_STATE_FILE}"
	[ -f "$state_file" ] || return 1
	cat "$state_file"
}

_prediction_id_from_state_json() {
	python3 -c 'import json,sys; print(json.load(sys.stdin).get("prediction_id",""))' 2>/dev/null
}

_fetch_remote_prediction_json() {
	local prediction_id="$1"
	[ -n "$prediction_id" ] || return 1
	local response=""
	response=$(curl -sf --max-time 15 -G \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		--data-urlencode "broadcaster_id=${BROADCASTER_ID}" \
		--data-urlencode "first=20" 2>/dev/null) || return 1
	python3 - "$response" "$prediction_id" <<'PY' 2>/dev/null
import json
import sys

data = json.loads(sys.argv[1])
prediction_id = sys.argv[2]

for item in data.get("data", []):
    if str(item.get("id", "")) == prediction_id:
        print(json.dumps(item))
        raise SystemExit(0)

raise SystemExit(1)
PY
}

_sync_prediction_state_with_remote() {
	[ -f "$PREDICTION_STATE_FILE" ] || return 1
	local state_json=""
	state_json=$(_load_prediction_state_json "$PREDICTION_STATE_FILE") || return 1
	local prediction_id=""
	prediction_id=$(echo "$state_json" | _prediction_id_from_state_json)
	if [ -z "$prediction_id" ]; then
		_log "SYNC: clearing local prediction state (invalid_state)"
		rm -f "$PREDICTION_STATE_FILE"
		return 0
	fi

	local remote_json=""
	remote_json=$(_fetch_remote_prediction_json "$prediction_id") || return 1
	local remote_status=""
	remote_status=$(echo "$remote_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)

	case "$remote_status" in
	ACTIVE | LOCKED)
		return 1
		;;
	RESOLVED | CANCELED)
		_log "SYNC: clearing local prediction state (remote_status=${remote_status})"
		rm -f "$PREDICTION_STATE_FILE"
		return 0
		;;
	"")
		return 1
		;;
	*)
		_log "SYNC: clearing local prediction state (remote_status=${remote_status})"
		rm -f "$PREDICTION_STATE_FILE"
		return 0
		;;
	esac
}

_prediction_status() {
	local response_file response_code api_message
	response_file=$(mktemp "$PREDICTION_RETRY_DIR/status.XXXXXX" 2>/dev/null || echo "$PREDICTION_RETRY_DIR/status.$$")
	response_code=$(curl -sS --max-time 15 -o "$response_file" -w '%{http_code}' -G \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		--data-urlencode "broadcaster_id=${BROADCASTER_ID}" \
		--data-urlencode "first=20" 2>/dev/null || echo 000)
	case "$response_code" in ''|*[!0-9]*) response_code=000 ;; esac
	if [ "$response_code" -lt 200 ] 2>/dev/null || [ "$response_code" -ge 300 ] 2>/dev/null; then
		api_message=$(_prediction_api_error_message "$response_file")
		python3 - "$response_code" "$api_message" "$EXPLORE_MODE_ACTIVE" <<'PY'
import json, sys
code, message, explore = sys.argv[1:4]
print(json.dumps({"ok": False, "enabled": True, "configured": True,
                  "explore_mode": explore == "true", "http_code": int(code),
                  "data": [], "error": message[:240] or "prediction_api_error"}, ensure_ascii=False))
PY
		rm -f "$response_file"
		return 0
	fi
	python3 - "$response_file" "$EXPLORE_MODE_ACTIVE" <<'PY'
import json, sys
path, explore = sys.argv[1:3]
try:
    payload = json.load(open(path, encoding="utf-8"))
except Exception:
    payload = {}
data = payload.get("data", []) if isinstance(payload, dict) else []
pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
print(json.dumps({"ok": True, "enabled": True, "configured": True,
                  "explore_mode": explore == "true", "http_code": 200,
                  "data": data if isinstance(data, list) else [],
                  "pagination": pagination if isinstance(pagination, dict) else {}}, ensure_ascii=False))
PY
	rm -f "$response_file"
}

# --- azumagdev 自動投票 (Twitch GQL API) ---
_auto_vote_prediction() {
	local state_json="$1"
	local bot_token="${TWITCH_BOT_TOKEN:-}"
	[ -n "$bot_token" ] || return 0
	bot_token="${bot_token#oauth:}"

	# 少し待ってから投票（予想が確実に受付中になるのを待つ）
	sleep 5

	# GQL APIにはファーストパーティトークンが必要
	# TWITCH_BOT_GQL_TOKEN が設定されていれば残高の10%を賭ける
	# 未設定ならフォールバックで AUTO_VOTE_POINTS を使う
	local gql_token="${TWITCH_BOT_GQL_TOKEN:-}"
	local vote_token="$bot_token"
	local vote_points="$AUTO_VOTE_POINTS"
	local channel_login="${TWITCH_CHANNEL_LOGIN:-azumagbanjo}"

	if [ -n "$gql_token" ]; then
		gql_token="${gql_token#oauth:}"
		vote_token="$gql_token"
		local balance_resp
		balance_resp=$(curl -sf --max-time 10 -X POST \
			"https://gql.twitch.tv/gql" \
			-H "Authorization: OAuth ${gql_token}" \
			-H "Client-Id: kimne78kx3ncx6brgo4mv6wki5h1ko" \
			-H "Content-Type: application/json" \
			-d "{\"query\":\"query { channel(name: \\\"${channel_login}\\\") { self { communityPoints { balance } } } }\"}" 2>/dev/null)

		vote_points=$(echo "$balance_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    balance = d['data']['channel']['self']['communityPoints']['balance']
    print(max(10, int(balance * 0.10)))
except Exception:
    print($AUTO_VOTE_POINTS)
" 2>/dev/null || echo "$AUTO_VOTE_POINTS")
		_log "AUTO_VOTE: GQL balance → betting ${vote_points}pt (10%)"
	else
		_log "AUTO_VOTE: no GQL token, using fixed ${vote_points}pt"
	fi

	# ランダムに1つの outcome を選ぶ
	local event_id outcome_id
	event_id=$(echo "$state_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prediction_id"])' 2>/dev/null)
	outcome_id=$(echo "$state_json" | python3 -c '
import json, sys, random
d = json.load(sys.stdin)
ids = d.get("outcome_ids", [])
if ids:
    print(random.choice(ids))
' 2>/dev/null)

	if [ -z "$event_id" ] || [ -z "$outcome_id" ]; then
		_log "AUTO_VOTE: skip (missing ids)"
		return 0
	fi

	# Twitch GQL API で投票（直接ミューテーション）
	local tx_id gql_payload
	tx_id=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
	gql_payload=$(python3 -c "
import json
print(json.dumps({
    'query': 'mutation { makePrediction(input: {eventID: \"$event_id\", outcomeID: \"$outcome_id\", points: $vote_points, transactionID: \"$tx_id\"}) { prediction { id } error { code } } }'
}))
" 2>/dev/null)

	local vote_resp
	vote_resp=$(curl -sf --max-time 10 -X POST \
		"https://gql.twitch.tv/gql" \
		-H "Authorization: OAuth ${vote_token}" \
		-H "Client-Id: kimne78kx3ncx6brgo4mv6wki5h1ko" \
		-H "Content-Type: application/json" \
		-d "$gql_payload" 2>/dev/null)

	if [ $? -eq 0 ] && [ -n "$vote_resp" ]; then
		local voted_label
		voted_label=$(echo "$state_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ids = d.get('outcome_ids', [])
labels = ['建国なし', 'ロシア建国', 'ソ連建国', '粛清']
idx = ids.index('$outcome_id') if '$outcome_id' in ids else -1
print(labels[idx] if 0 <= idx < len(labels) else 'unknown')
" 2>/dev/null)
		_log "AUTO_VOTE: azumagdev voted '${voted_label}' (${vote_points}pt)"
	else
		_log "AUTO_VOTE: failed (curl error or empty response)"
	fi
}

# --- サブコマンド ---
case "${1:-}" in
create)
	GAME_NUM="${2:-0}"
	if _prediction_retry_active create; then
		_log "SKIP: prediction create retry backoff active ($(_prediction_retry_remaining create)s)"
		exit 0
	fi
	_sync_prediction_state_with_remote || true

	# 既存の予想が残っていたらスキップ
	if [ -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: prediction already active"
		cat "$PREDICTION_STATE_FILE"
		exit 0
	fi
	# ローカル state を失った再起動でも、Twitch 側の ACTIVE/LOCKED 予想を
	# 先に取り込み、同じサイクルで二重作成しない。
	if _recover_remote_active_prediction; then
		cat "$PREDICTION_STATE_FILE"
		exit 0
	fi

	# JSON ペイロード生成
	payload=$(
		python3 - "$BROADCASTER_ID" "$PREDICTION_WINDOW_SEC" "$PREDICTION_MAX_GAMES" <<'PY'
import json, sys
bid, window, n_games = sys.argv[1], int(sys.argv[2]), sys.argv[3]
print(json.dumps({
    "broadcaster_id": bid,
    "title": f"{n_games}ゲーム中に建国できる？",
    "outcomes": [
        {"title": "建国なし"},
        {"title": "ロシア建国(ソ連不成立)"},
        {"title": "ソ連建国"},
        {"title": "粛清"}
    ],
    "prediction_window": window
}))
PY
	)

	response_file=$(mktemp "$PREDICTION_RETRY_DIR/create.XXXXXX" 2>/dev/null || echo "$PREDICTION_RETRY_DIR/create.$$")
	response_code=$(curl -sS --max-time 15 -o "$response_file" -w '%{http_code}' -X POST \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" 2>/dev/null || echo 000)
	response=$(cat "$response_file" 2>/dev/null || true)
	if [ "$response_code" -lt 200 ] 2>/dev/null || [ "$response_code" -ge 300 ] 2>/dev/null || [ -z "$response" ]; then
		api_message=$(_prediction_api_error_message "$response_file")
		# 同一チャンネルに既にACTIVE/LOCKED予想がある場合はTwitchが400を返す。
		# これは正常な競合なのでWARNではなくINFOに留め、リモートを同期してローカルを復元する。
		if printf '%s' "$api_message" | grep -qi "already" || { [ "$response_code" = "400" ] && printf '%s' "$response" | grep -qi "already"; }; then
			_log "INFO: prediction already active on Twitch, syncing remote state"
			if _recover_remote_active_prediction; then
				rm -f "$response_file"
				_prediction_retry_clear create
				cat "$PREDICTION_STATE_FILE"
				exit 0
			fi
		fi
		delay=$(_prediction_retry_record create "$response_code" "$api_message")
		_log "WARN: prediction create failed (http=${response_code:-000}, message=${api_message:-unknown}, retry=${delay}s)"
		rm -f "$response_file"
		exit 1
	fi
	rm -f "$response_file"

	# レスポンスから prediction_id と outcome_ids を抽出
	result=$(
		python3 - "$response" "$GAME_NUM" <<'PY' 2>/dev/null
import json, sys, time
data = json.loads(sys.argv[1])
pred = data.get("data", [{}])[0]
pred_id = pred.get("id", "")
outcomes = pred.get("outcomes", [])
outcome_ids = [o.get("id", "") for o in outcomes]
if not pred_id or len(outcome_ids) < 4:
    sys.exit(1)
state = {
    "prediction_id": pred_id,
    "outcome_ids": outcome_ids,
    "game_num": int(sys.argv[2]),
    "created_at": int(time.time()),
    "russia_created": False
}
print(json.dumps(state))
PY
	)

	if [ $? -ne 0 ] || [ -z "$result" ]; then
		delay=$(_prediction_retry_record create "$response_code" "invalid prediction response")
		_log "WARN: failed to parse prediction response (retry=${delay}s)"
		exit 1
	fi
	_prediction_retry_clear create

	mkdir -p "$(dirname "$PREDICTION_STATE_FILE")"
	echo "$result" >"$PREDICTION_STATE_FILE"
	_log "prediction created: $(echo "$result" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"id={d[\"prediction_id\"]}")' 2>/dev/null)"
	# 受付時間の表示: 60秒以上なら「N分」、未満なら「N秒」
	if [ "$PREDICTION_WINDOW_SEC" -ge 60 ]; then
		_window_display="$((PREDICTION_WINDOW_SEC / 60))分"
	else
		_window_display="${PREDICTION_WINDOW_SEC}秒"
	fi
	enqueue_chat_message "チャネルポイント予想スタート！「${PREDICTION_MAX_GAMES}ゲーム中に建国できる？」投票受付中（${_window_display}） ※ソ連建国・粛清は即確定。ロシア建国は${PREDICTION_MAX_GAMES}ゲーム後にソ連不成立なら的中" "predictions"

	# azumagdev ボットがランダムに1票入れる（GQL API）
	# 独立した再実行可能なサブコマンドとして起動し、親シェル終了の影響を受けにくくする。
	nohup "$0" autovote "$PREDICTION_STATE_FILE" >>tmp/auto_vote.log 2>&1 </dev/null &
	disown || true

	echo "$result"
	;;

resolve)
	OUTCOME_INDEX="${2:-0}"
	if _prediction_retry_active resolve; then
		_log "SKIP: prediction resolve retry backoff active ($(_prediction_retry_remaining resolve)s)"
		exit 0
	fi

	if [ ! -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: no active prediction"
		exit 0
	fi

	state=$(cat "$PREDICTION_STATE_FILE")
	PREDICTION_ID=$(echo "$state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prediction_id"])' 2>/dev/null)
	WINNING_OUTCOME_ID=$(
		python3 - "$state" "$OUTCOME_INDEX" <<'PY' 2>/dev/null
import json, sys
data = json.loads(sys.argv[1])
print(data["outcome_ids"][int(sys.argv[2])])
PY
	)

	if [ -z "$PREDICTION_ID" ] || [ -z "$WINNING_OUTCOME_ID" ]; then
		_log "WARN: invalid state file"
		rm -f "$PREDICTION_STATE_FILE"
		exit 1
	fi

	payload=$(
		python3 - "$BROADCASTER_ID" "$PREDICTION_ID" "$WINNING_OUTCOME_ID" <<'PY'
import json, sys
bid, pid, wid = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "broadcaster_id": bid,
    "id": pid,
    "status": "RESOLVED",
    "winning_outcome_id": wid
}))
PY
	)

	response_file=$(mktemp "$PREDICTION_RETRY_DIR/resolve.XXXXXX" 2>/dev/null || echo "$PREDICTION_RETRY_DIR/resolve.$$")
	response_code=$(curl -sS --max-time 15 -o "$response_file" -w '%{http_code}' -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" 2>/dev/null || echo 000)

	if [ "$response_code" -lt 200 ] 2>/dev/null || [ "$response_code" -ge 300 ] 2>/dev/null; then
		api_message=$(_prediction_api_error_message "$response_file")
		delay=$(_prediction_retry_record resolve "$response_code" "$api_message")
		if _sync_prediction_state_with_remote; then
			_log "WARN: prediction resolve failed, but remote sync cleared local state"
			_prediction_retry_clear resolve
			rm -f "$response_file"
			exit 0
		fi
		if _clear_stale_prediction_state_if_any; then
			_log "WARN: prediction resolve failed, but stale local state was cleared"
			_prediction_retry_clear resolve
			rm -f "$response_file"
			exit 0
		fi
		_log "WARN: prediction resolve failed (http=${response_code:-000}, message=${api_message:-unknown}, retry=${delay}s)"
		rm -f "$response_file"
		exit 1
	fi
	rm -f "$response_file"
	_prediction_retry_clear resolve

	OUTCOME_LABELS=("建国なし" "ロシア建国(ソ連不成立)" "ソ連建国" "粛清")
	OUTCOME_LABEL="${OUTCOME_LABELS[$OUTCOME_INDEX]:-index=$OUTCOME_INDEX}"
	_log "prediction resolved: $OUTCOME_LABEL"
	if [ "${OUTCOME_INDEX}" = "3" ] && [ -f "$PREDICTION_STATE_FILE" ]; then
		# 粛清理由は detail を追加で投稿
		_regression_detail=$(python3 - "$PREDICTION_STATE_FILE" 2>/dev/null <<'PY'
import json, sys
f = sys.argv[1]
try:
    d = json.load(open(f))
    # 視聴者向けに整形済みのラベルのみ使う(生コードは出さない)
    label = d.get("regression_reason_label", "")
    if label:
        print(f"理由：{label}")
except Exception:
    pass
PY
)
		[ -n "${_regression_detail}" ] || _regression_detail=""
		if [ -n "${_regression_detail}" ]; then
			enqueue_chat_message "予想結果：「${OUTCOME_LABEL}」！試していた新戦略が前より成績を落としたので、安定版に戻しました。${_regression_detail}" "predictions"
		else
			enqueue_chat_message "予想結果：「${OUTCOME_LABEL}」！試していた新戦略が前より成績を落としたので、安定版に戻しました。" "predictions"
		fi
	else
		enqueue_chat_message "予想結果：「${OUTCOME_LABEL}」でした！" "predictions"
	fi
	rm -f "$PREDICTION_STATE_FILE"
	printf '{"ok":true,"status":"resolved","outcome_index":%s,"outcome_label":"%s"}\n' "$OUTCOME_INDEX" "$OUTCOME_LABEL"
	;;

cancel)
	if [ ! -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: no active prediction"
		exit 0
	fi

	state=$(cat "$PREDICTION_STATE_FILE")
	PREDICTION_ID=$(echo "$state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prediction_id"])' 2>/dev/null)

	if [ -z "$PREDICTION_ID" ]; then
		_log "WARN: invalid state file"
		rm -f "$PREDICTION_STATE_FILE"
		exit 1
	fi

	payload=$(
		python3 - "$BROADCASTER_ID" "$PREDICTION_ID" <<'PY'
import json, sys
bid, pid = sys.argv[1], sys.argv[2]
print(json.dumps({
    "broadcaster_id": bid,
    "id": pid,
    "status": "CANCELED"
}))
PY
	)

	curl -sf --max-time 15 -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" >/dev/null 2>&1

	if [ $? -ne 0 ]; then
		if _sync_prediction_state_with_remote; then
			_log "WARN: prediction cancel failed, but remote sync cleared local state"
			exit 0
		fi
		if _clear_stale_prediction_state_if_any; then
			_log "WARN: prediction cancel failed, but stale local state was cleared"
			exit 0
		fi
		_log "WARN: prediction cancel failed"
		exit 1
	fi

	_log "prediction canceled"
	rm -f "$PREDICTION_STATE_FILE"
	printf '%s\n' '{"ok":true,"status":"canceled"}'
	;;

autovote)
	STATE_FILE="${2:-$PREDICTION_STATE_FILE}"
	state_json=$(_load_prediction_state_json "$STATE_FILE") || {
		_log "AUTO_VOTE: skip (missing state file: ${STATE_FILE})"
		exit 0
	}
	_log "AUTO_VOTE: start"
	_auto_vote_prediction "$state_json"
	;;

cleanup)
	_clear_stale_prediction_state_if_any || true
	_sync_prediction_state_with_remote || true
	;;

status)
	_prediction_status
	;;

sync)
	if _recover_remote_active_prediction; then
		python3 - "$PREDICTION_STATE_FILE" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data = {}
print(json.dumps({"ok": True, "adopted": True, "state": data}, ensure_ascii=False))
PY
	else
		printf '%s\n' '{"ok":true,"adopted":false,"message":"no_active_remote_prediction"}'
	fi
	;;

*)
	echo "Usage: $0 {create|resolve <outcome_index>|cancel|status|sync|autovote [state_file]|cleanup [game_num]}" >&2
	echo "  outcome_index: 0=建国なし, 1=ロシア建国, 2=ソ連建国, 3=粛清" >&2
	exit 1
	;;
esac
