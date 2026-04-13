#!/bin/bash
# repair_wave_link.sh - Restart Elgato Wave Link via normal macOS quit/open.

set -uo pipefail

cd "$(dirname "$0")"

APP_NAME="${WAVE_LINK_APP_NAME:-Elgato Wave Link}"
REOPEN="${WAVE_LINK_REPAIR_REOPEN:-1}"
WAIT_SEC="${WAVE_LINK_REPAIR_WAIT_SEC:-3}"
LOG_FILE="${WAVE_LINK_REPAIR_LOG_FILE:-tmp/debug/wave_link_repair.log}"

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
	printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"
}

case "$WAIT_SEC" in
'' | *[!0-9]*) WAIT_SEC=3 ;;
esac

if ! command -v osascript >/dev/null 2>&1; then
	log "ERROR: osascript not found"
	exit 1
fi

log "request: app=${APP_NAME} reopen=${REOPEN} wait=${WAIT_SEC}s"

if ! osascript - "$APP_NAME" <<'OSA' >>"$LOG_FILE" 2>&1
on run argv
	set appName to item 1 of argv
	tell application appName to quit
end run
OSA
then
	log "ERROR: quit failed app=${APP_NAME}"
	exit 1
fi

sleep "$WAIT_SEC"

case "$REOPEN" in
0 | false | no)
	log "done: quit only app=${APP_NAME}"
	exit 0
	;;
esac

if open -a "$APP_NAME" >>"$LOG_FILE" 2>&1; then
	log "done: reopened app=${APP_NAME}"
	exit 0
fi

log "ERROR: reopen failed app=${APP_NAME}"
exit 1
