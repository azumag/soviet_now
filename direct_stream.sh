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

# The parent owns the optional common foreground. Merely deploying this code
# does not arm/restart the live encoder or change TwiCa ownership.
DOCICH_ROOT="${DOCICH_ROOT:-/home/ubuntu/docich}"
if [ -x "$DOCICH_ROOT/bin/docich-twica-ffmpeg" ] &&
   PYTHONPATH="$DOCICH_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m docich.twica_operator pipeline-enabled >/dev/null 2>&1; then
	export DOCICH_TWICA_REAL_FFMPEG="${SOREN_DIRECT_STREAM_FFMPEG_BIN:-ffmpeg}"
	export SOREN_DIRECT_STREAM_FFMPEG_BIN="$DOCICH_ROOT/bin/docich-twica-ffmpeg"
fi

exec python3 "$SCRIPT_DIR/lib/direct_stream.py" "$@"
