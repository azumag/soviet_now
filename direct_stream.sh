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

case "${DOCICH_TWICA_COMMON_ENABLED:-0}" in
  1|true|on)
    DOCICH_ROOT="${DOCICH_ROOT:-/home/ubuntu/docich}"
    [ -f "$DOCICH_ROOT/src/docich/twica_stream.py" ] || { echo 'common TwiCa adapter unavailable' >&2; exit 2; }
    export PYTHONPATH="$DOCICH_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
    exec python3 -m docich.twica_stream --runner "$SCRIPT_DIR/lib/direct_stream.py" "$@"
    ;;
esac
exec python3 "$SCRIPT_DIR/lib/direct_stream.py" "$@"
