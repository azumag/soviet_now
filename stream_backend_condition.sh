#!/bin/bash
# systemd ExecCondition helper. It selects a backend without sourcing .env, so
# a malformed or attacker-controlled value can never execute shell code here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
EXPECTED="${1:-}"

case "$EXPECTED" in
obs|ffmpeg) ;;
*)
	echo "usage: $0 obs|ffmpeg" >&2
	exit 2
	;;
esac

BACKEND=$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
backend = "obs"
if path.exists():
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise SystemExit(2)
    for line in lines:
        match = re.match(
            r"^[ \t]*(?:export[ \t]+)?SOREN_STREAM_BACKEND[ \t]*=[ \t]*(.*?)[ \t]*$",
            line,
        )
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        backend = value.strip().lower()
print(backend)
PY
) || {
	echo "stream backend environment is unreadable" >&2
	exit 2
}

case "$BACKEND" in
obs|ffmpeg) ;;
*)
	echo "stream backend environment is invalid" >&2
	exit 2
	;;
esac

[ "$BACKEND" = "$EXPECTED" ]
