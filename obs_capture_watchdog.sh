#!/bin/bash
# obs_capture_watchdog.sh — periodic self-heal for the OBS game window capture.
#
# Every OBS_CAPTURE_WATCHDOG_INTERVAL seconds (default 90): if the `sorengame`
# source is visible, run obs_capture_watchdog_check.mjs which (1) rebinds the
# capture to the live Chrome window if the bound macOS window is stale/dead/wrong
# for the current display mode (the classic symptom after a Chrome crash/restart;
# mode = china/meriken from tmp/state/soren_display_mode), and (2) bounces the
# macOS screen_capture if the stream is frozen while the game advances. It only
# acts when the binding is actually wrong or the output is actually frozen, so
# there is no flicker during normal operation, and it never blanks a last-good
# capture when no live window matches yet.
#
# Background daemon: singleton via pidfile, honors tmp/stop. Started by
# soren_loop.sh / start_all.sh; can also be run standalone.
set -u
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

INTERVAL="${OBS_CAPTURE_WATCHDOG_INTERVAL:-90}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=90 ;; esac
SCENE="${OBS_DASHBOARD_SCENE:-soren}"
GAME_SOURCE="${SOREN_GAME_OBS_SOURCE:-${OBS_GAME_SOURCE:-${SOREN_OBS_GAME_SOURCE_NAME:-sorengame}}}"
GAME_STATE_MAX_AGE="${OBS_CAPTURE_WATCHDOG_GAME_MAX_AGE:-90}"
PIDFILE="tmp/state/obs_capture_watchdog.pid"
LOG="logs/obs_capture_watchdog.log"
mkdir -p tmp/state logs 2>/dev/null || true

# singleton
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
	exit 0
fi
echo $$ >"$PIDFILE"
trap 'rm -f "$PIDFILE" 2>/dev/null || true' EXIT
# INT/TERM must actually terminate the daemon (not just run the EXIT trap and keep
# looping), otherwise a restart leaves the old loop alive with stale code/interval.
trap 'exit 143' TERM
trap 'exit 130' INT

_log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true; }

_game_state_fresh() {
	[ -f game_state.json ] || return 1
	local age now mt
	now=$(date +%s)
	mt=$(stat -f %m game_state.json 2>/dev/null || echo 0)
	age=$(( now - mt ))
	[ "$age" -le "$GAME_STATE_MAX_AGE" ]
}

_sorengame_visible() {
	[ -x ./obs_control.sh ] || return 1
	./obs_control.sh status "$SCENE" "$GAME_SOURCE" 2>/dev/null | grep -q "${GAME_SOURCE}=on"
}

_log "watchdog start interval=${INTERVAL}s source=${GAME_SOURCE}"
while true; do
	sleep "$INTERVAL" &
	wait $! 2>/dev/null || true
	[ -f tmp/stop ] && { _log "tmp/stop detected; exiting"; break; }
	if ! _sorengame_visible; then
		_log "skip: ${GAME_SOURCE} not visible (improve/param session?)"
		continue
	fi
	out=$(node obs_capture_watchdog_check.mjs 2>&1)
	rc=$?
	case "$rc" in
		10) _log "FROZEN -> bounced. ${out##*$'\n'}" ;;
		11) _log "STALE -> rebound. ${out##*$'\n'}" ;;
		0)  _log "ok. ${out##*$'\n'}" ;;
		*)  _log "check rc=$rc: ${out##*$'\n'}" ;;
	esac
done
