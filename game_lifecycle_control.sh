#!/bin/bash
# Fixed control surface for docich.  No caller-supplied command is evaluated.
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source ./eloop_lib.sh

case "${1:-}" in
stop-after-boundary)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	[ "$(game_lifecycle_request_id 2>/dev/null || true)" = "$request_id" ] || exit 3
	game_lifecycle_stop_after_boundary
	;;
fresh-start)
	request_id="${2:-}"
	[ -n "$request_id" ] || exit 4
	python3 ./lib/game_lifecycle.py --root "$ROOT" fresh-start --request-id "$request_id"
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
