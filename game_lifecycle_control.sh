#!/bin/bash
# Fixed control surface for docich.  No caller-supplied command is evaluated.
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source ./eloop_lib.sh

# --- shared overlay lifecycle (Issue #303 / option A) ------------------------
# The independent shared overlay (soren-shared-overlay.service) is NOT started
# at boot: while the main SorenGame is displayed its opaque blank stage covers
# the game. It is only needed during game-only mode, where it provides the
# common rails and the /healthz readiness gate the bridge requires before an
# irreversible game stop. So it is started here when game-only mode begins and
# stopped again when the main game returns.
SOREN_SHARED_OVERLAY_UNIT="${SOREN_SHARED_OVERLAY_UNIT:-soren-shared-overlay.service}"
SOREN_SHARED_OVERLAY_HEALTH_URL="${SOREN_SHARED_OVERLAY_HEALTH_URL:-http://127.0.0.1:8092/healthz}"

_shared_overlay_enabled() {
	case "${SOREN_SHARED_OVERLAY_ENABLED:-${SOREN_GAME_LIFECYCLE_SHARED_OVERLAY:-}}" in
	1 | true | yes | on | TRUE | True | YES | Yes | ON | On) return 0 ;;
	esac
	return 1
}

shared_overlay_start() {
	_shared_overlay_enabled || return 0
	curl -fsS --max-time 2 "$SOREN_SHARED_OVERLAY_HEALTH_URL" >/dev/null 2>&1 && return 0
	sudo -n systemctl start "$SOREN_SHARED_OVERLAY_UNIT" >/dev/null 2>&1 || true
	local i
	for i in $(seq 1 40); do
		curl -fsS --max-time 2 "$SOREN_SHARED_OVERLAY_HEALTH_URL" >/dev/null 2>&1 && return 0
		sleep 0.5
	done
	return 1
}

shared_overlay_stop() {
	_shared_overlay_enabled || return 0
	sudo -n systemctl stop "$SOREN_SHARED_OVERLAY_UNIT" >/dev/null 2>&1 || true
	return 0
}

case "${1:-}" in
stop-after-boundary)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	[ "$(game_lifecycle_request_id 2>/dev/null || true)" = "$request_id" ] || exit 3
	# The bridge refuses the irreversible game stop unless the shared overlay
	# gate is ready, so bring the overlay up first (game-only mode begins).
	shared_overlay_start || true
	game_lifecycle_stop_after_boundary
	;;
fresh-start)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	if python3 ./lib/game_lifecycle.py --root "$ROOT" fresh-start --request-id "$request_id"; then
		# Main game is back: the shared overlay would cover it, so stop it.
		shared_overlay_stop
	fi
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
	shared_overlay_stop
	python3 ./lib/game_lifecycle.py --root "$ROOT" status
	;;
status)
	python3 ./lib/game_lifecycle.py --root "$ROOT" status
	;;
*) exit 4 ;;
esac
