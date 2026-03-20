#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG_FILE="$SCRIPT_DIR/tmp/soren91.log"
STOP_FILE="$SCRIPT_DIR/tmp/stop"
RETRY_DELAY_SEC="${SOREN91_RESTART_DELAY_SEC:-3}"

mkdir -p "$SCRIPT_DIR/tmp" 2>/dev/null || true

attempt=0
while true; do
	[ -f "$STOP_FILE" ] && exit 0

	attempt=$((attempt + 1))
	printf '[%s] [runner] launch attempt=%d\n' "$(date '+%H:%M:%S')" "$attempt" >>"$LOG_FILE" 2>/dev/null || true

	SOREN91_EXTERNAL_IMPROVE=1 node main.mjs >>"$LOG_FILE" 2>&1
	rc=$?

	[ -f "$STOP_FILE" ] && exit 0

	printf '[%s] [runner] node exited rc=%s, retry in %ss\n' \
		"$(date '+%H:%M:%S')" "$rc" "$RETRY_DELAY_SEC" >>"$LOG_FILE" 2>/dev/null || true
	sleep "$RETRY_DELAY_SEC"
done
