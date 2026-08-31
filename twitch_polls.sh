#!/bin/bash
# twitch_polls.sh - Twitch Polls API wrapper (docich#8)
# Usage:
#   ./twitch_polls.sh live
#   ./twitch_polls.sh create DRAFT_JSON_FILE
#   ./twitch_polls.sh status [POLL_ID]

set -o pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

COMMAND="${1:-status}"
STATE_DIR="${TMP_STATE_DIR:-tmp/state}"
STATE_FILE="${TWITCH_POLL_STATE_FILE:-$STATE_DIR/current_poll.json}"
TOKEN="${TWITCH_POLLS_TOKEN:-${TWITCH_PREDICTIONS_TOKEN:-}}"
TOKEN="${TOKEN#oauth:}"
CLIENT_ID="${TWITCH_CLIENT_ID:-}"
BROADCASTER_ID="${TWITCH_BROADCASTER_ID:-}"

_json_error() {
	python3 - "$1" "${2:-}" <<'PY'
import json, sys
print(json.dumps({"ok": False, "error": sys.argv[1], "detail": sys.argv[2][:240]}, ensure_ascii=False))
PY
}

if [ "${TWITCH_POLLS_ENABLED:-0}" != "1" ]; then
	_json_error disabled
	exit 0
fi
if [ -z "$TOKEN" ] || [ -z "$CLIENT_ID" ] || [ -z "$BROADCASTER_ID" ]; then
	_json_error missing_configuration
	exit 0
fi
if [ "${EXPLORE_MODE:-0}" = "1" ] && [ "$COMMAND" != "status" ] && [ "$COMMAND" != "live" ]; then
	_json_error explore_mode
	exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || true

_request() {
	local method="$1" url="$2" output="$3" payload="${4:-}" code
	if [ -n "$payload" ]; then
		code=$(curl -sS --max-time 20 -o "$output" -w '%{http_code}' -X "$method" "$url" \
			-H "Authorization: Bearer ${TOKEN}" \
			-H "Client-Id: ${CLIENT_ID}" \
			-H "Content-Type: application/json" \
			-d "$payload" 2>/dev/null || echo 000)
	else
		code=$(curl -sS --max-time 20 -o "$output" -w '%{http_code}' -X "$method" "$url" \
			-H "Authorization: Bearer ${TOKEN}" \
			-H "Client-Id: ${CLIENT_ID}" 2>/dev/null || echo 000)
	fi
	case "$code" in ''|*[!0-9]*) code=000 ;; esac
	printf '%s\n' "$code"
}

_api_error() {
	python3 - "$1" "$2" <<'PY'
import json, sys
code, path = sys.argv[1:3]
try:
    body = json.load(open(path, encoding="utf-8"))
except Exception:
    body = {}
message = str(body.get("message") or body.get("error") or "request_failed")
print(json.dumps({"ok": False, "http_code": int(code or 0), "error": message[:240]}, ensure_ascii=False))
PY
}

case "$COMMAND" in
live)
	tmp=$(mktemp "$STATE_DIR/.poll_live.XXXXXXXX") || exit 1
	code=$(_request GET "https://api.twitch.tv/helix/streams?user_id=${BROADCASTER_ID}" "$tmp")
	if [ "$code" != "200" ]; then _api_error "$code" "$tmp"; rm -f "$tmp"; exit 0; fi
	python3 - "$tmp" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8")).get("data", [])
print(json.dumps({"ok": True, "live": bool(data)}, ensure_ascii=False))
PY
	rm -f "$tmp"
	;;
create)
	draft_file="${2:-}"
	if [ ! -s "$draft_file" ]; then _json_error missing_draft; exit 0; fi
	payload=$(python3 - "$draft_file" "$BROADCASTER_ID" "${TWITCH_POLL_DURATION_SEC:-120}" <<'PY' 2>/dev/null
import json, sys
path, broadcaster_id, duration_raw = sys.argv[1:4]
d = json.load(open(path, encoding="utf-8"))
title = " ".join(str(d.get("title", "")).split())[:60]
raw = d.get("choices", [])
choices = []
seen = set()
for item in raw if isinstance(raw, list) else []:
    value = " ".join(str(item).split())[:25]
    key = value.casefold()
    if value and key not in seen:
        seen.add(key)
        choices.append({"title": value})
if not title or not 2 <= len(choices) <= 5:
    raise SystemExit(2)
duration = max(15, min(1800, int(duration_raw)))
print(json.dumps({"broadcaster_id": broadcaster_id, "title": title,
                  "choices": choices, "duration": duration}, ensure_ascii=False))
PY
)
	if [ -z "$payload" ]; then _json_error invalid_draft; exit 0; fi
	tmp=$(mktemp "$STATE_DIR/.poll_create.XXXXXXXX") || exit 1
	code=$(_request POST "https://api.twitch.tv/helix/polls" "$tmp" "$payload")
	if [ "$code" != "200" ]; then _api_error "$code" "$tmp"; rm -f "$tmp"; exit 0; fi
	python3 - "$tmp" "$STATE_FILE" <<'PY'
import json, os, sys, time
response_path, state_path = sys.argv[1:3]
item = (json.load(open(response_path, encoding="utf-8")).get("data") or [{}])[0]
state = {"poll": item, "phase": "active", "created_at_epoch": int(time.time())}
tmp = state_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False)
os.replace(tmp, state_path)
print(json.dumps({"ok": True, "poll": item}, ensure_ascii=False))
PY
	rm -f "$tmp"
	;;
status)
	poll_id="${2:-}"
	if [ -z "$poll_id" ] && [ -f "$STATE_FILE" ]; then
		poll_id=$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("poll") or {}).get("id", ""))' "$STATE_FILE" 2>/dev/null || true)
	fi
	url="https://api.twitch.tv/helix/polls?broadcaster_id=${BROADCASTER_ID}&first=20"
	[ -n "$poll_id" ] && url="${url}&id=${poll_id}"
	tmp=$(mktemp "$STATE_DIR/.poll_status.XXXXXXXX") || exit 1
	code=$(_request GET "$url" "$tmp")
	if [ "$code" != "200" ]; then _api_error "$code" "$tmp"; rm -f "$tmp"; exit 0; fi
	python3 - "$tmp" "$poll_id" <<'PY'
import json, sys
path, poll_id = sys.argv[1:3]
items = json.load(open(path, encoding="utf-8")).get("data", [])
if poll_id:
    items = [x for x in items if str(x.get("id", "")) == poll_id]
else:
    active = [x for x in items if x.get("status") == "ACTIVE"]
    items = active[:1]
print(json.dumps({"ok": True, "poll": items[0] if items else None}, ensure_ascii=False))
PY
	rm -f "$tmp"
	;;
*)
	_json_error unknown_command "$COMMAND"
	;;
esac
