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

TOKEN="${TWITCH_BOT_TOKEN:-}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"
if [ -z "$TOKEN" ] || [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
	_log "SKIP: missing env vars (TWITCH_BOT_TOKEN, TWITCH_CLIENT_ID, TWITCH_BROADCASTER_ID)"
	exit 0
fi
TOKEN="${TOKEN#oauth:}"

PREDICTION_STATE_FILE="tmp/state/current_prediction.json"
PREDICTION_WINDOW_SEC="${TWITCH_PREDICTION_WINDOW_SEC:-180}"

# --- JSON パーサー (jq 不要) ---
_json_get() {
	python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d$1 if d$1 else '')" 2>/dev/null
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

	response=$(curl -sf -X POST \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$(python3 -c "
import json
print(json.dumps({
    'broadcaster_id': '${BROADCASTER_ID}',
    'title': '次のゲームで建国できる？',
    'outcomes': [
        {'title': '建国なし'},
        {'title': 'ロシア建国'},
        {'title': 'ソ連建国'},
        {'title': '粛清される'}
    ],
    'prediction_window': ${PREDICTION_WINDOW_SEC}
}))
")" 2>/dev/null)

	if [ $? -ne 0 ] || [ -z "$response" ]; then
		_log "WARN: prediction create failed"
		exit 1
	fi

	# レスポンスから prediction_id と outcome_ids を抽出
	result=$(python3 -c "
import json, sys, time
data = json.loads(sys.stdin.read())
pred = data.get('data', [{}])[0]
pred_id = pred.get('id', '')
outcomes = pred.get('outcomes', [])
outcome_ids = [o.get('id', '') for o in outcomes]
if not pred_id or len(outcome_ids) < 4:
    print('ERROR', file=sys.stderr)
    sys.exit(1)
state = {
    'prediction_id': pred_id,
    'outcome_ids': outcome_ids,
    'game_num': ${GAME_NUM},
    'created_at': int(time.time())
}
print(json.dumps(state))
" <<< "$response" 2>/dev/null)

	if [ $? -ne 0 ] || [ -z "$result" ]; then
		_log "WARN: failed to parse prediction response"
		exit 1
	fi

	mkdir -p "$(dirname "$PREDICTION_STATE_FILE")"
	echo "$result" > "$PREDICTION_STATE_FILE"
	_log "prediction created: $(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'id={d[\"prediction_id\"]}')" 2>/dev/null)"
	echo "$result"
	;;

resolve)
	OUTCOME_INDEX="${2:-0}"

	if [ ! -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: no active prediction"
		exit 0
	fi

	state=$(cat "$PREDICTION_STATE_FILE")
	PREDICTION_ID=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin)['prediction_id'])" 2>/dev/null)
	WINNING_OUTCOME_ID=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin)['outcome_ids'][${OUTCOME_INDEX}])" 2>/dev/null)

	if [ -z "$PREDICTION_ID" ] || [ -z "$WINNING_OUTCOME_ID" ]; then
		_log "WARN: invalid state file"
		rm -f "$PREDICTION_STATE_FILE"
		exit 1
	fi

	response=$(curl -sf -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$(python3 -c "
import json
print(json.dumps({
    'broadcaster_id': '${BROADCASTER_ID}',
    'id': '${PREDICTION_ID}',
    'status': 'RESOLVED',
    'winning_outcome_id': '${WINNING_OUTCOME_ID}'
}))
")" 2>/dev/null)

	if [ $? -ne 0 ]; then
		_log "WARN: prediction resolve failed"
		exit 1
	fi

	OUTCOME_LABELS=("建国なし" "ロシア建国" "ソ連建国" "粛清される")
	_log "prediction resolved: ${OUTCOME_LABELS[$OUTCOME_INDEX]:-index=$OUTCOME_INDEX}"
	rm -f "$PREDICTION_STATE_FILE"
	;;

cancel)
	if [ ! -f "$PREDICTION_STATE_FILE" ]; then
		_log "SKIP: no active prediction"
		exit 0
	fi

	state=$(cat "$PREDICTION_STATE_FILE")
	PREDICTION_ID=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin)['prediction_id'])" 2>/dev/null)

	if [ -z "$PREDICTION_ID" ]; then
		_log "WARN: invalid state file"
		rm -f "$PREDICTION_STATE_FILE"
		exit 1
	fi

	curl -sf -X PATCH \
		"https://api.twitch.tv/helix/predictions" \
		-H "Authorization: Bearer ${TOKEN}" \
		-H "Client-Id: ${CLIENT_ID}" \
		-H "Content-Type: application/json" \
		-d "$(python3 -c "
import json
print(json.dumps({
    'broadcaster_id': '${BROADCASTER_ID}',
    'id': '${PREDICTION_ID}',
    'status': 'CANCELED'
}))
")" >/dev/null 2>&1

	_log "prediction canceled"
	rm -f "$PREDICTION_STATE_FILE"
	;;

*)
	echo "Usage: $0 {create|resolve <outcome_index>|cancel}" >&2
	echo "  outcome_index: 0=建国なし, 1=ロシア建国, 2=ソ連建国, 3=粛清される" >&2
	exit 1
	;;
esac
