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
status)
	python3 ./lib/game_lifecycle.py --root "$ROOT" status
	;;
*) exit 4 ;;
esac
