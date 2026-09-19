#!/bin/bash
# Fixed control surface for docich.  No caller-supplied command is evaluated.
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source ./eloop_lib.sh

# --- common rails (soren-shared-overlay.service) -----------------------------
# The independent common overlay is ALWAYS ON (enabled at boot, never toggled
# from this control surface).  It renders the shared rails and serves the
# /healthz readiness gate the bridge checks before an irreversible game stop.
#
# Earlier this surface started the overlay on stop-after-boundary and stopped
# it when the main game returned (Issue #303 option A).  That ordering is not
# safe: the overlay is a fullscreen window, so starting it while the main game
# window is already mapped covers the live game.  On 2026-09-15 the scheduled
# soren91 corner blacked out the broadcast for four minutes that way.
# start_shared_overlay_service.sh keeps the overlay window below the game
# windows, so staying up costs nothing while the main game is displayed.
#
# Readiness is still verified rather than assumed: an overlay that is down
# leaves the bridge free to answer `unsupported` and keep the old game
# (fail-open), which is the recovery path the lifecycle already documents.
SOREN_SHARED_OVERLAY_HEALTH_URL="${SOREN_SHARED_OVERLAY_HEALTH_URL:-http://127.0.0.1:8092/healthz}"

shared_overlay_ready() {
	curl -fsS --max-time 2 "$SOREN_SHARED_OVERLAY_HEALTH_URL" >/dev/null 2>&1
}

# Wait out a service restart window before handing the request to the bridge.
# The overlay is expected to be ready already; this never starts the unit.
shared_overlay_wait_ready() {
	shared_overlay_ready && return 0
	local i
	for i in $(seq 1 40); do
		shared_overlay_ready && return 0
		sleep 0.5
	done
	return 1
}

case "${1:-}" in
stop-after-boundary)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	[ "$(game_lifecycle_request_id 2>/dev/null || true)" = "$request_id" ] || exit 3
	# The bridge refuses the irreversible game stop unless the shared overlay
	# gate is ready.
	shared_overlay_wait_ready || true
	game_lifecycle_stop_after_boundary
	;;
fresh-start)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	python3 ./lib/game_lifecycle.py --root "$ROOT" fresh-start --request-id "$request_id"
	;;
player-commit)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	[ "$(game_lifecycle_request_id 2>/dev/null || true)" = "$request_id" ] || exit 3
	game_lifecycle_commit_player
	;;
cancel)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	[ "$(game_lifecycle_request_id 2>/dev/null || true)" = "$request_id" ] || exit 3
	python3 ./lib/game_lifecycle.py --root "$ROOT" cancel --request-id "$request_id" || exit $?
	# Cancellation can arrive after a controller was interrupted midway through
	# game-only teardown.  The game loop/watchdog may already be gone, so the
	# fixed control surface itself must remove only this request's owned gates;
	# relying on a stopped worker to observe the cancel cannot recover.
	game_lifecycle_restore_watchdog
	game_lifecycle_restore_predictions
	game_lifecycle_restore_improvements
	game_lifecycle_restore_loop
	python3 ./lib/game_lifecycle.py --root "$ROOT" status
	;;
status)
	python3 ./lib/game_lifecycle.py --root "$ROOT" status
	;;
*) exit 4 ;;
esac
