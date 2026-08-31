#!/bin/bash
# lib/twitch_ads.sh - Twitch ad snooze during TTS speaking
# Requires: TWITCH_BOT_TOKEN, TWITCH_CLIENT_ID, TWITCH_BROADCASTER_ID
# API: POST /helix/channels/ads/schedule/snooze (channel:manage:ads)
#      GET  /helix/channels/ads (channel:read:ads) for snooze_count/next_ad_at

TWITCH_ADS_ENABLED="${TWITCH_ADS_ENABLED:-1}"
TWITCH_ADS_SNOOZE_THRESHOLD_SEC="${TWITCH_SNOOZE_THRESHOLD_SEC:-600}"
TWITCH_ADS_POLL_SEC="${TWITCH_SNOOZE_POLL_SEC:-240}"
TWITCH_ADS_DEBUG_LOG="${TWITCH_ADS_DEBUG_LOG:-tmp/debug/twitch_ads.log}"
TWITCH_ADS_LAST_SNOOZE_FILE="${TWITCH_ADS_LAST_SNOOZE_FILE:-tmp/state/last_snooze_at}"
TWITCH_ADS_LAST_NEXT_AD_FILE="${TWITCH_ADS_LAST_NEXT_AD_FILE:-tmp/state/last_next_ad_at}"
TWITCH_ADS_BACKOFF_FILE="${TWITCH_ADS_BACKOFF_FILE:-tmp/state/twitch_ads_backoff_until}"

_twitch_ads_log() {
	local msg="$1"
	mkdir -p "$(dirname "$TWITCH_ADS_DEBUG_LOG")" 2>/dev/null || true
	printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$msg" >>"$TWITCH_ADS_DEBUG_LOG" 2>/dev/null || true
}

_twitch_ads_backoff_active() {
	[ -f "$TWITCH_ADS_BACKOFF_FILE" ] || return 1
	local until now
	until=$(cat "$TWITCH_ADS_BACKOFF_FILE" 2>/dev/null || echo 0)
	case "$until" in ''|*[!0-9]*) return 1 ;; esac
	now=$(date +%s)
	[ "$until" -gt "$now" ]
}

_twitch_ads_set_backoff() {
	local sec="${1:-60}"
	local until=$(( $(date +%s) + sec ))
	mkdir -p "$(dirname "$TWITCH_ADS_BACKOFF_FILE")" 2>/dev/null || true
	printf '%s\n' "$until" > "$TWITCH_ADS_BACKOFF_FILE" 2>/dev/null || true
}

_twitch_ads_get_status() {
	local token="${TWITCH_BOT_TOKEN:-}"
	local client_id="${TWITCH_CLIENT_ID:-}"
	local broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
	[ -n "$token" ] && [ -n "$client_id" ] && [ -n "$broadcaster_id" ] || return 2
	token="${token#oauth:}"
	local resp http_code body
	resp=$(curl -s -w "\n%{http_code}" --max-time 5 \
		-H "Authorization: Bearer ${token}" \
		-H "Client-Id: ${client_id}" \
		"https://api.twitch.tv/helix/channels/ads?broadcaster_id=${broadcaster_id}" 2>/dev/null) || return 1
	http_code=$(printf '%s' "$resp" | tail -n1)
	body=$(printf '%s' "$resp" | sed '$d')
	if [ "$http_code" != "200" ]; then
		_twitch_ads_log "GET ads failed HTTP=$http_code body=$(printf '%s' "$body" | head -c 200)"
		if [ "$http_code" = "401" ]; then _twitch_ads_set_backoff 3600; fi
		return 1
	fi
	# parse with python
	python3 - "$body" <<'PY' 2>/dev/null
import json, sys
body=sys.argv[1]
try:
    data=json.loads(body)
    arr=data.get("data") or []
    if not arr:
        print("no_data")
        raise SystemExit(0)
    d=arr[0]
    print(f"snooze_count={d.get('snooze_count','')}")
    print(f"next_ad_at={d.get('next_ad_at','')}")
    print(f"snooze_refresh_at={d.get('snooze_refresh_at','')}")
    print(f"duration={d.get('duration','')}")
except Exception as e:
    print(f"parse_error={e}")
    raise SystemExit(1)
PY
	return $?
}

_twitch_ads_snooze_once() {
	local token="${TWITCH_BOT_TOKEN:-}"
	local client_id="${TWITCH_CLIENT_ID:-}"
	local broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
	[ -n "$token" ] && [ -n "$client_id" ] && [ -n "$broadcaster_id" ] || return 2
	token="${token#oauth:}"
	local resp http_code body
	resp=$(curl -s -w "\n%{http_code}" --max-time 5 -X POST \
		-H "Authorization: Bearer ${token}" \
		-H "Client-Id: ${client_id}" \
		"https://api.twitch.tv/helix/channels/ads/schedule/snooze?broadcaster_id=${broadcaster_id}" 2>/dev/null) || return 1
	http_code=$(printf '%s' "$resp" | tail -n1)
	body=$(printf '%s' "$resp" | sed '$d')
	if [ "$http_code" = "200" ] || [ "$http_code" = "204" ]; then
		_twitch_ads_log "snooze success HTTP=$http_code"
		mkdir -p "$(dirname "$TWITCH_ADS_LAST_SNOOZE_FILE")" 2>/dev/null || true
		date +%s > "$TWITCH_ADS_LAST_SNOOZE_FILE" 2>/dev/null || true
		return 0
	fi
	_twitch_ads_log "snooze failed HTTP=$http_code body=$(printf '%s' "$body" | head -c 200)"
	if [ "$http_code" = "429" ]; then
		# parse snooze_refresh_at if available, else backoff 300s
		local refresh
		refresh=$(printf '%s' "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('data') or [{}])[0].get('snooze_refresh_at','') if isinstance(d.get('data'), list) else '')" 2>/dev/null || echo "")
		_twitch_ads_set_backoff 300
	elif [ "$http_code" = "401" ]; then
		_twitch_ads_set_backoff 3600
	elif [ "$http_code" = "400" ]; then
		# not live or no scheduled ad - not an error, just no-op, short backoff to avoid spam
		_twitch_ads_set_backoff 60
	fi
	return 1
}

# public: twitch_ads_maybe_snooze [reason]
#   Checks if ad is scheduled within threshold and snooze_count >0, then snoozes.
#   Dedupes on same next_ad_at.
twitch_ads_maybe_snooze() {
	local reason="${1:-speaking}"
	# WebUI toggle must also take effect in an already-running long TTS poller.
	# Reload .env before checking the flag; only then consider credentials/API work.
	[ -f .env ] && set -a && . ./.env 2>/dev/null; set +a || true
	[ "${TWITCH_ADS_ENABLED:-1}" = "1" ] || return 0
	_twitch_ads_backoff_active && return 0
	local token="${TWITCH_BOT_TOKEN:-}"
	local client_id="${TWITCH_CLIENT_ID:-}"
	local broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
	if [ -z "$token" ] || [ -z "$client_id" ] || [ -z "$broadcaster_id" ]; then
		return 0
	fi
	# get status
	local status_output
	status_output=$(_twitch_ads_get_status 2>&1) || return 0
	local snooze_count next_ad_at snooze_refresh_at
	snooze_count=$(printf '%s' "$status_output" | sed -n 's/^snooze_count=//p' | head -n1)
	next_ad_at=$(printf '%s' "$status_output" | sed -n 's/^next_ad_at=//p' | head -n1)
	snooze_refresh_at=$(printf '%s' "$status_output" | sed -n 's/^snooze_refresh_at=//p' | head -n1)
	# no scheduled ad
	if [ -z "$next_ad_at" ] || [ "$next_ad_at" = "null" ] || [ "$next_ad_at" = "None" ]; then
		return 0
	fi
	# check snooze_count
	case "$snooze_count" in ''|*[!0-9]*) snooze_count=0 ;; esac
	if [ "$snooze_count" -le 0 ]; then
		_twitch_ads_log "skip snooze: count 0 next_ad=$next_ad_at reason=$reason"
		return 0
	fi
	# dedupe on same next_ad_at
	if [ -f "$TWITCH_ADS_LAST_NEXT_AD_FILE" ]; then
		local last_next
		last_next=$(cat "$TWITCH_ADS_LAST_NEXT_AD_FILE" 2>/dev/null || echo "")
		if [ "$last_next" = "$next_ad_at" ]; then
			return 0
		fi
	fi
	# check threshold: next_ad_at - now < threshold
	local now_sec next_sec diff
	now_sec=$(date +%s)
	# parse next_ad_at: epoch int または RFC3339
	next_sec=$(python3 - "$next_ad_at" <<'PY' 2>/dev/null
import sys, datetime
s=str(sys.argv[1]).strip()
# epoch 整数を優先
try:
    # 浮動小数も許容しつつ整数秒へ
    if s.lstrip("-").replace(".", "", 1).isdigit():
        # 10桁前後の epoch のみを整数とみなす (RFC3339 の年は4桁)
        v = float(s)
        if 1e9 <= v <= 4e9:
            print(int(v))
            raise SystemExit(0)
except Exception:
    pass
try:
    if s.endswith("Z"):
        s=s[:-1]+"+00:00"
    dt=datetime.datetime.fromisoformat(s)
    print(int(dt.timestamp()))
except Exception:
    print(0)
PY
) || next_sec=0
	case "$next_sec" in ''|*[!0-9]*|0) return 0 ;; esac
	diff=$((next_sec - now_sec))
	local threshold="${TWITCH_SNOOZE_THRESHOLD_SEC:-600}"
	case "$threshold" in ''|*[!0-9]*) threshold=600 ;; esac
	if [ "$diff" -gt "$threshold" ] || [ "$diff" -lt -60 ]; then
		# too far or already passed
		return 0
	fi
	# attempt snooze
	if _twitch_ads_snooze_once; then
		mkdir -p "$(dirname "$TWITCH_ADS_LAST_NEXT_AD_FILE")" 2>/dev/null || true
		printf '%s' "$next_ad_at" > "$TWITCH_ADS_LAST_NEXT_AD_FILE" 2>/dev/null || true
		_twitch_ads_log "snoozed next_ad=$next_ad_at count_before=$snooze_count reason=$reason diff=${diff}s"
		return 0
	fi
	return 1
}
