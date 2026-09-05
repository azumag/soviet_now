#!/bin/bash
# start_shared_overlay_service.sh - independent common broadcast display.
#
# NOT owned by the game loop, the game bridge watchdog, or the game-switch
# coordinator: a game-only stop (claim-stop -> Unity Quit -> browser close)
# never touches this service, and this service never stops game resources.
# It renders the common rails/sidebar surfaces and serves the readiness gate
# (/healthz) that the game bridge must pass before any irreversible game stop.
#
# Display safety: this wrapper starts its OWN Xvfb on SOREN_SHARED_OVERLAY_DISPLAY
# (default :98). It never kills processes discovered by name or display number —
# a display-scoped cleanup would kill the production Xvfb (:99, owned by the
# streaming runtime) when pointed at the stream display (2026-09-05 near-miss).
# Restart cleanup only touches the pids recorded in our own pidfile.
set -u
cd /home/ubuntu/soren || exit 1

set -a
# shellcheck disable=SC1091
. ./.env
set +a

export SOREN_SHARED_OVERLAY_PORT="${SOREN_SHARED_OVERLAY_PORT:-8092}"
export SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED="${SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:-1}"
export SOREN_DIRECT_STATS_OVERLAY_HTML_FILE="${SOREN_DIRECT_STATS_OVERLAY_HTML_FILE:-tmp/state/status_overlay.html}"
export SOREN_DIRECT_OPS_OVERLAY_HTML_FILE="${SOREN_DIRECT_OPS_OVERLAY_HTML_FILE:-tmp/state/show_status_overlay.html}"
export IMPROVE_STATE_FILE="${IMPROVE_STATE_FILE:-tmp/state/improve_state.json}"
export IMPROVE_AI_LOG_FILE="${IMPROVE_AI_LOG_FILE:-tmp/state/improve_daemon.log}"
export WILDCARD_PARALLEL_STATUS_FILE="${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}"
export SOREN_ACTIVE_GAME_CONTEXT_FILE="${SOREN_ACTIVE_GAME_CONTEXT_FILE:-/home/ubuntu/docich/run-soren-live/game_switch.json}"
# The game bridge owns the twica proxy on its own port (production default
# 18080). This service needs its own proxy instance for its page's twica
# surface; sharing the bridge's port would EADDRINUSE-crash this service
# every restart (observed 2026-09-05 17:50), so force a dedicated port.
export SOREN_DIRECT_TWICA_PROXY_PORT=18081

DISP="${SOREN_SHARED_OVERLAY_DISPLAY:-:98}"
export HOME="${HOME:-/home/ubuntu}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
PIDFILE="tmp/shared_overlay_service.pids"
XVFB_PID=""
WM_PID=""

# ATTACH=1: render on an already-running foreign display (e.g. the stream
# display :99). Never starts Xvfb or a window manager there, and never
# kills anything discovered by name/display — cleanup stays pidfile-scoped.
ATTACH="${SOREN_SHARED_OVERLAY_ATTACH:-0}"

# Restart cleanup: only the pids our previous instance recorded.
if [ -f "$PIDFILE" ]; then
	while read -r p _; do
		case "$p" in ''|*[!0-9]*) continue ;; esac
		kill -9 "$p" 2>/dev/null || true
	done <"$PIDFILE"
	rm -f "$PIDFILE"
fi

if [ "$ATTACH" = "1" ]; then
	if [ ! -e "/tmp/.X11-unix/X${DISP#:}" ]; then
		echo "[SHARED-OVERLAY-SERVICE] attach: display $DISP does not exist" >&2
		exit 1
	fi
else
	case "$DISP" in
	:98) rm -f "/tmp/.X98-lock" ;;
	:97) rm -f "/tmp/.X97-lock" ;;
	esac
	Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp >tmp/shared_overlay_xvfb.log 2>&1 &
	XVFB_PID=$!
	sleep 1
	if ! kill -0 "$XVFB_PID" 2>/dev/null; then
		echo "[SHARED-OVERLAY-SERVICE] Xvfb $DISP failed to start" >&2
		exit 1
	fi
	DISPLAY="$DISP" xfwm4 --replace >tmp/shared_overlay_wm.log 2>&1 &
	WM_PID=$!
	sleep 3
fi

DISPLAY="$DISP" node shared_overlay.mjs >>tmp/shared_overlay.log 2>&1 &
NODE_PID=$!
# Keep the game presentation window above the overlay chrome so the common
# rails/sidebar show around it while the game area stays visible. Scoped to
# docich-present* windows only (the docich game presentation).
(
	while :; do
		for wid in $(DISPLAY="$DISP" xdotool search --name "^docich-present" 2>/dev/null); do
			DISPLAY="$DISP" wmctrl -i -r "$wid" -b add,above 2>/dev/null || true
		done
		sleep 5
	done
) &
KEEP_ABOVE_PID=$!
printf '%s\n' "$XVFB_PID" "$WM_PID" "$NODE_PID" "$KEEP_ABOVE_PID" >"$PIDFILE"
cleanup() {
	for pid in "$NODE_PID" "${XVFB_PID:-}" "${WM_PID:-}" "$KEEP_ABOVE_PID"; do
		case "$pid" in ''|*[!0-9]*) continue ;; esac
		kill -TERM "$pid" 2>/dev/null || true
	done
	sleep 1
	for pid in "$NODE_PID" "${XVFB_PID:-}" "${WM_PID:-}" "$KEEP_ABOVE_PID"; do
		case "$pid" in ''|*[!0-9]*) continue ;; esac
		kill -9 "$pid" 2>/dev/null || true
	done
}
trap cleanup TERM INT EXIT
wait "$NODE_PID"
