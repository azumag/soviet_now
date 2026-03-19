#!/bin/bash
# twitch_predictions.sh - Twitch チャネルポイント予想 (Predictions) API wrapper
# Usage:
#   ./twitch_predictions.sh create              → prediction_id と outcome_ids を stdout に JSON 出力
#   ./twitch_predictions.sh resolve <outcome_index>  → 勝者 outcome で解決 (0-3)
#   ./twitch_predictions.sh cancel              → キャンセル
#
# 状態ファイル: tmp/state/current_prediction.json
cd "$(dirname "$0")"

# 単体実行時にも.envを読めるようにする
[ -z "${TWITCH_CLIENT_ID:-}" ] && [ -f .env ] && set -a && . ./.env && set +a

_log() { echo "[twitch_predictions $(date '+%H:%M:%S')] $*" >&2; }

# --- 環境変数チェック ---
if [ "${TWITCH_PREDICTIONS_ENABLED:-0}" != "1" ]; then
	_log "SKIP: TWITCH_PREDICTIONS_ENABLED is not 1"
	exit 0
fi

# 予想はチャネルオーナーのトークンが必須 (TWITCH_PREDICTIONS_TOKEN)
# チャット投稿用の TWITCH_BOT_TOKEN とは別
TOKEN="${TWITCH_PREDICTIONS_TOKEN:-}"
if [ -z "$TOKEN" ]; then
	_log "SKIP: TWITCH_PREDICTIONS_TOKEN not set"
	exit 0
fi
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
	_log "SKIP: missing env vars (TWITCH_CLIENT_ID, TWITCH_BROADCASTER_ID)"
	exit 0
fi
TOKEN="${TOKEN#oauth:}"

PREDICTION_STATE_FILE="tmp/state/current_prediction.json"
PREDICTION_WINDOW_SEC="${TWITCH_PREDICTION_WINDOW_SEC:-480}"
AUTO_VOTE_POINTS="${TWITCH_AUTO_VOTE_POINTS:-10}"

# --- azumagdev 自動投票 (Twitch GQL API) ---
_auto_vote_prediction() {
	local state_json="$1"
	local bot_token="${TWITCH_BOT_TOKEN:-}"
	[ -n "$bot_token" ] || return 0
	bot_token="${bot_token#oauth:}"

	# 少し待ってから投票（予想が確実に受付中になるのを待つ）
	sleep 5

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

	# Twitch GQL API で投票
	local gql_payload
	gql_payload=$(python3 -c "
import json
print(json.dumps({
    'operationName': 'MakePrediction',
    'variables': {
        'input': {
            'eventID': '$event_id',
            'outcomeID': '$outcome_id',
            'points': $AUTO_VOTE_POINTS,
            'transactionID': '$(python3 -c "import uuid; print(str(uuid.uuid4()))")'
        }
    },
    'extensions': {
        'persistedQuery': {
            'version': 1,
            'sha256Hash': 'b44682ecc88358817009f20571c0b1b81e1e3292a7157a9c9ee0b290e4c26c09'
        }
    }
}))
" 2>/dev/null)

	local vote_resp
	vote_resp=$(curl -sf --max-time 10 -X POST \
		"https://gql.twitch.tv/gql" \
		-H "Authorization: OAuth ${bot_token}" \
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
		_log "AUTO_VOTE: azumagdev voted '${voted_label}' (${AUTO_VOTE_POINTS}pt)"
	else
		_log "AUTO_VOTE: failed (curl error or empty response)"
	fi
}

# --- サブコマンド ---
case "${1:-}" in
create)
	# 既存の予想が残っていたらスキップ
	if [ -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: prediction already active"
		cat "$PREDICTION_STATE_FILE"
		exit 0
	fi

	GAME_NUM="${2:-0}"

	# JSON ペイロード生成
	payload=$(python3 - "$BROADCASTER_ID" "$PREDICTION_WINDOW_SEC" <<'PY'
import json, sys
bid, window = sys.argv[1], int(sys.argv[2])
print(json.dumps({
    "broadcaster_id": bid,
    "title": "12ゲーム中に建国できる？",
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

	response=$(curl -sf --max-time 15 -X POST \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" 2>/dev/null)

	if [ $? -ne 0 ] || [ -z "$response" ]; then
		_log "WARN: prediction create failed"
		exit 1
	fi

	# レスポンスから prediction_id と outcome_ids を抽出
	result=$(python3 - "$response" "$GAME_NUM" <<'PY' 2>/dev/null
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
    "created_at": int(time.time())
}
print(json.dumps(state))
PY
)

	if [ $? -ne 0 ] || [ -z "$result" ]; then
		_log "WARN: failed to parse prediction response"
		exit 1
	fi

	mkdir -p "$(dirname "$PREDICTION_STATE_FILE")"
	echo "$result" > "$PREDICTION_STATE_FILE"
	_log "prediction created: $(echo "$result" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"id={d[\"prediction_id\"]}")' 2>/dev/null)"
	./twitch_chat.sh send "チャネルポイント予想スタート！「12ゲーム中に建国できる？」投票受付中（$((PREDICTION_WINDOW_SEC / 60))分） ※ソ連建国・粛清は即確定。ロシア建国は12ゲーム後にソ連不成立なら的中" 2>/dev/null &

	# azumagdev ボットがランダムに1票入れる（GQL API）
	_auto_vote_prediction "$result" &

	echo "$result"
	;;

resolve)
	OUTCOME_INDEX="${2:-0}"

	if [ ! -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: no active prediction"
		exit 0
	fi

	state=$(cat "$PREDICTION_STATE_FILE")
	PREDICTION_ID=$(echo "$state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prediction_id"])' 2>/dev/null)
	WINNING_OUTCOME_ID=$(python3 - "$state" "$OUTCOME_INDEX" <<'PY' 2>/dev/null
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

	payload=$(python3 - "$BROADCASTER_ID" "$PREDICTION_ID" "$WINNING_OUTCOME_ID" <<'PY'
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

	response=$(curl -sf --max-time 15 -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$payload" 2>/dev/null)

	if [ $? -ne 0 ]; then
		_log "WARN: prediction resolve failed"
		exit 1
	fi

	OUTCOME_LABELS=("建国なし" "ロシア建国(ソ連不成立)" "ソ連建国" "粛清")
	OUTCOME_LABEL="${OUTCOME_LABELS[$OUTCOME_INDEX]:-index=$OUTCOME_INDEX}"
	_log "prediction resolved: $OUTCOME_LABEL"
	./twitch_chat.sh send "予想結果：「${OUTCOME_LABEL}」でした！" 2>/dev/null &
	rm -f "$PREDICTION_STATE_FILE"
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

	payload=$(python3 - "$BROADCASTER_ID" "$PREDICTION_ID" <<'PY'
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

	_log "prediction canceled"
	rm -f "$PREDICTION_STATE_FILE"
	;;

*)
	echo "Usage: $0 {create|resolve <outcome_index>|cancel}" >&2
	echo "  outcome_index: 0=建国なし, 1=ロシア建国, 2=ソ連建国, 3=粛清" >&2
	exit 1
	;;
esac
