#!/bin/bash
# Keep the OBS main window out of the XSHM full-screen capture.
# OBS may honor --minimize-to-tray before a window is ever mapped; that already
# satisfies the goal and must not fail ExecStartPost (which would restart OBS).

set -u
export DISPLAY="${DISPLAY:-:99}"
attempts="${OBS_HIDE_UI_ATTEMPTS:-30}"
poll_sec="${OBS_HIDE_UI_POLL_SEC:-1}"

for _ in $(seq 1 "$attempts"); do
	windows=$(wmctrl -lx 2>/dev/null || true)
	window_id=$(printf '%s\n' "$windows" | awk 'tolower($3) ~ /(^|\.)obs(\.|$)/ { print $1; exit }')
	if [ -n "$window_id" ]; then
		wmctrl -i -r "$window_id" -b add,hidden 2>/dev/null || true
		if command -v xdotool >/dev/null 2>&1; then
			xdotool windowminimize "$window_id" 2>/dev/null || true
		fi
		# XSHM captures the whole display, so put the game back in front after
		# minimizing OBS. This is a one-shot startup action, not a watchdog.
		game_id=$(printf '%s\n' "$windows" | awk 'tolower($3) ~ /chromium/ { print $1; exit }')
		if [ -n "$game_id" ]; then
			wmctrl -i -a "$game_id" 2>/dev/null || true
		fi
		exit 0
	fi
	# With --minimize-to-tray there is normally no mapped OBS window at all.
	# Give startup a short grace period in case Qt maps it slightly later.
	sleep "$poll_sec"
done

# No visible/mapped OBS window is the desired outcome, not a startup failure.
exit 0
