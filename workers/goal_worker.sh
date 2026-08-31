#!/bin/bash
# Twitch Creator Goals を監視し、初回の達成境界だけをAIコメント経路へ流す。

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
[ -f .env ] && set -a && . ./.env && set +a

WORKER_NAME="goal_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
STATE_FILE="${TWITCH_GOALS_STATE_FILE:-tmp/state/twitch_goals.json}"
RAW_LOG="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}/raw.log"
INTERVAL="${TWITCH_GOALS_POLL_SEC:-60}"
ONCE=0
[ "${1:-}" = "--once" ] && ONCE=1
_STOPPED=0
_RELOAD_REQUESTED=0

_log() { echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"; }
_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	[ "$(cat "$PID_FILE" 2>/dev/null || true)" = "$$" ] && rm -f "$PID_FILE"
}
_stop() { _cleanup; trap - EXIT; exit 130; }
_reload() { _RELOAD_REQUESTED=1; }
trap '_cleanup' EXIT
trap '_stop' INT TERM
trap '_reload' HUP USR1

_format_goal_event() {
	python3 -c 'import json,re,sys
d=json.loads(sys.argv[1])
labels={"follower":"フォロワー", "subscription":"サブスクポイント", "subscription_count":"サブスク", "new_subscription":"新規サブスクポイント", "new_subscription_count":"新規サブスク", "new_bit":"Bits", "new_cheerer":"応援者"}
label=labels.get(str(d.get("type", "")), "配信")
desc=re.sub(r"[\\x00-\\x1f`$\\\\{}|;<>&]", "", str(d.get("description", "")))
desc=" ".join(desc.split())[:80]
current=int(d.get("current_amount", 0)); target=int(d.get("target_amount", 0))
name=(f"「{desc}」" if desc else "")
print(f"配信目標: [配信目標達成] {label}目標{name}を{current}/{target}で達成しました。みんなと一緒に一度だけ祝ってください。")' "$1"
}

_poll_once() {
	[ "${TWITCH_GOALS_ENABLED:-0}" = "1" ] || return 0
	[ "${EXPLORE_MODE:-0}" != "1" ] || return 0
	local token="${TWITCH_GOALS_TOKEN:-${TWITCH_PREDICTIONS_TOKEN:-}}"
	local client_id="${TWITCH_CLIENT_ID:-}" broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
	if [ -z "$token" ] || [ -z "$client_id" ] || [ -z "$broadcaster_id" ]; then
		_log "SKIP: missing Twitch goals credentials/config"
		return 0
	fi
	token="${token#oauth:}"
	local response events http_code body event line
	response=$(mktemp "tmp/.goal_response.XXXXXXXX") || return 1
	events=$(mktemp "tmp/.goal_events.XXXXXXXX") || { rm -f "$response"; return 1; }
	http_code=$(curl -sS -o "$response" -w '%{http_code}' \
		-H "Authorization: Bearer ${token}" -H "Client-Id: ${client_id}" \
		"https://api.twitch.tv/helix/goals?broadcaster_id=${broadcaster_id}" 2>/dev/null || true)
	if [ "$http_code" != "200" ]; then
		_log "WARN: Twitch goals request failed (HTTP=${http_code:-network})"
		rm -f "$response" "$events"
		return 0
	fi
	if ! python3 lib/twitch_goal_monitor.py "$response" "$STATE_FILE" >"$events"; then
		_log "WARN: invalid Twitch goals response"
		rm -f "$response" "$events"
		return 0
	fi
	mkdir -p "$(dirname "$RAW_LOG")"
	while IFS= read -r event; do
		[ -n "$event" ] || continue
		line=$(_format_goal_event "$event") || continue
		printf '%s\n' "$line" >>"$RAW_LOG"
		_log "goal completion queued for AI response"
	done <"$events"
	rm -f "$response" "$events"
}

mkdir -p "$(dirname "$PID_FILE")"
if [ "$ONCE" -eq 0 ]; then
	if [ -f "$PID_FILE" ]; then
		old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
		case "$old_pid" in
			''|*[!0-9]*) ;;
			*)
				if kill -0 "$old_pid" 2>/dev/null; then
					_log "already running (PID=$old_pid) -> no-op"
					exit 0
				fi
				;;
		esac
	fi
	echo $$ >"$PID_FILE"
fi

while :; do
	if [ "$_RELOAD_REQUESTED" -eq 1 ]; then
		_RELOAD_REQUESTED=0
		[ -f .env ] && set -a && . ./.env && set +a
		INTERVAL="${TWITCH_GOALS_POLL_SEC:-60}"
		_log "reload complete (interval=${INTERVAL}s enabled=${TWITCH_GOALS_ENABLED:-0})"
	fi
	_poll_once
	[ "$ONCE" -eq 1 ] && break
	sleep "$INTERVAL" & wait $!
done
