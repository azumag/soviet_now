#!/bin/bash
# Explicitly confirmed visible flash/tone acceptance for the live direct path.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

case "${1:-}" in
--config)
	ACTION=config
	;;
--analyze)
	ACTION=analyze
	shift
	;;
--run)
	if [ "${2:-}" != "--confirm-live-av-sync-test" ] || [ -n "${3:-}" ]; then
		echo "Live A/V probe requires: --run --confirm-live-av-sync-test" >&2
		exit 2
	fi
	ACTION=run
	;;
*)
	echo "Usage: $0 --config | --run --confirm-live-av-sync-test | --analyze CAPTURE --output-dir DIR" >&2
	exit 2
	;;
esac

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
fi

if [ "$ACTION" = "analyze" ]; then
	exec python3 "$SCRIPT_DIR/lib/direct_av_sync.py" analyze "$@"
fi
exec python3 "$SCRIPT_DIR/lib/direct_av_sync.py" "$ACTION"
