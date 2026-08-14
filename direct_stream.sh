#!/bin/bash
# Linux FFmpeg direct-stream entrypoint. Destination credentials must remain in
# the local RTMP relay; this process accepts loopback output URLs only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
fi

exec python3 "$SCRIPT_DIR/lib/direct_stream.py" "$@"
