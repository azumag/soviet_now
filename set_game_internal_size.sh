#!/bin/bash
# Change only the live Unity drawing-buffer size. The stream output stays
# 1280x720 and destination credentials are never read or printed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
STATE_FILE="${SOREN_INTERNAL_SIZE_STATE_FILE:-$SCRIPT_DIR/tmp/state/internal_resolution_change.json}"
RUNTIME_UNIT="${SOREN_RUNTIME_SYSTEMD_UNIT:-soren-runtime.service}"
READY_TIMEOUT_SEC="${SOREN_INTERNAL_SIZE_READY_TIMEOUT_SEC:-120}"

usage() {
	cat <<'EOF' >&2
Usage:
  ./set_game_internal_size.sh --print-plan WIDTHxHEIGHT
  ./set_game_internal_size.sh --apply WIDTHxHEIGHT --confirm-live-restart

Supported threshold-ladder sizes:
  576x324, 640x360, 704x396, 768x432, 832x468, 896x504,
  960x540, 1024x576, 1088x612, 1152x648, 1216x684, 1280x720
EOF
	exit 2
}

MODE="${1:-}"
RAW_SIZE="${2:-}"
CONFIRM="${3:-}"
[ -n "$MODE" ] && [ -n "$RAW_SIZE" ] && [ -z "${4:-}" ] || usage

SIZE=${RAW_SIZE//X/x}
SIZE=${SIZE//, /x}
SIZE=${SIZE//,/x}
case "$SIZE" in
576x324|640x360|704x396|768x432|832x468|896x504|960x540|1024x576|1088x612|1152x648|1216x684|1280x720) ;;
*)
	echo "unsupported internal size: $RAW_SIZE" >&2
	exit 2
	;;
esac
WIDTH=${SIZE%x*}
HEIGHT=${SIZE#*x}
ENV_VALUE="$WIDTH,$HEIGHT"

case "$MODE" in
--print-plan)
	[ -z "$CONFIRM" ] || usage
	python3 - "$WIDTH" "$HEIGHT" <<'PY'
import json
import sys
print(json.dumps({
    "action": "restart_soren_runtime",
    "destination_credentials_read": False,
    "output_size_unchanged": "1280x720",
    "requested_internal_size": f"{sys.argv[1]}x{sys.argv[2]}",
    "rollback_on_readiness_failure": True,
}, sort_keys=True))
PY
	exit 0
	;;
--apply)
	[ "$CONFIRM" = "--confirm-live-restart" ] || usage
	;;
*) usage ;;
esac

case "$READY_TIMEOUT_SEC" in
''|*[!0-9]*) echo "readiness timeout must be an integer" >&2; exit 2 ;;
esac
if [ "$READY_TIMEOUT_SEC" -lt 30 ] || [ "$READY_TIMEOUT_SEC" -gt 300 ]; then
	echo "readiness timeout must be between 30 and 300 seconds" >&2
	exit 2
fi
if [ "$(uname -s)" != "Linux" ]; then
	echo "live internal-size changes are Linux-only" >&2
	exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
	echo "missing environment file: $ENV_FILE" >&2
	exit 2
fi
for command_name in awk cp mktemp python3 sudo systemctl; do
	command -v "$command_name" >/dev/null 2>&1 || {
		echo "required command not found: $command_name" >&2
		exit 2
	}
done
for file in direct_stream_soak.sh direct_stream_status.sh; do
	[ -x "$file" ] || {
		echo "required executable not found: $file" >&2
		exit 2
	}
done
sudo -n true
if ! systemctl is-active --quiet "$RUNTIME_UNIT"; then
	echo "runtime service is not active: $RUNTIME_UNIT" >&2
	exit 2
fi
if ./direct_stream_soak.sh status | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("running") is True else 1)'; then
	echo "stop the active direct-stream monitor before changing internal size" >&2
	exit 2
fi

read_env_value() {
	local key="$1"
	python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import re
import shlex
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = ""
for line in path.read_text(encoding="utf-8").splitlines():
    match = re.match(rf"^[ \t]*{re.escape(key)}=(.*)$", line)
    if not match:
        continue
    parts = shlex.split(match.group(1), posix=True)
    value = parts[0] if parts else ""
    break
print(value)
PY
}

CURRENT_SIZE=$(read_env_value SOREN_GAME_INTERNAL_SIZE)
BACKEND=$(read_env_value SOREN_STREAM_BACKEND)
if [ "$BACKEND" != "ffmpeg" ]; then
	echo "internal-size threshold trials require SOREN_STREAM_BACKEND=ffmpeg" >&2
	exit 2
fi
if [ "$CURRENT_SIZE" = "$ENV_VALUE" ]; then
	echo "internal size is already $SIZE"
	exit 0
fi

mkdir -p "$(dirname "$STATE_FILE")" "$SCRIPT_DIR/tmp"
BACKUP="${ENV_FILE}.internal-size-backup.$(date '+%Y%m%d-%H%M%S')"
cp -p "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP" 2>/dev/null || true

set_env_value() {
	local key="$1" value="$2" quoted temporary
	quoted=${value//\'/\'\\\'\'}
	quoted="'$quoted'"
	temporary=$(mktemp "$SCRIPT_DIR/tmp/.env.internal-size.XXXXXX")
	awk -v key="$key" -v replacement="$key=$quoted" '
		BEGIN { replaced = 0 }
		$0 ~ "^[[:space:]]*" key "=" {
			if (!replaced) print replacement
			replaced = 1
			next
		}
		{ print }
		END { if (!replaced) print replacement }
	' "$ENV_FILE" >"$temporary"
	chmod 600 "$temporary"
	mv "$temporary" "$ENV_FILE"
}

restore_previous() {
	set +e
	cp -p "$BACKUP" "$ENV_FILE"
	sudo -n systemctl restart "$RUNTIME_UNIT"
	echo "internal-size change failed readiness; restored $CURRENT_SIZE" >&2
}

ROLLBACK_ARMED=1
cleanup() {
	local original_rc=$?
	trap - EXIT INT TERM
	if [ "$ROLLBACK_ARMED" -eq 1 ]; then
		restore_previous
	fi
	exit "$original_rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

set_env_value SOREN_GAME_INTERNAL_SIZE "$ENV_VALUE"
if ! sudo -n systemctl restart "$RUNTIME_UNIT"; then
	exit 1
fi

READY=0
DEADLINE=$(( $(date +%s) + READY_TIMEOUT_SEC ))
while [ "$(date +%s)" -le "$DEADLINE" ]; do
	if systemctl is-active --quiet "$RUNTIME_UNIT" \
		&& python3 - "$SCRIPT_DIR/tmp/state/game_render_health.json" "$WIDTH" "$HEIGHT" <<'PY' \
		&& ./direct_stream_status.sh | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("running") is True and float(value.get("fps", 0)) >= 29 else 1)'
import json
from pathlib import Path
import sys
import time

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    fresh = (time.time() * 1000) - float(value.get("lastFrameAt", 0)) < 15000
    ready = (
        int(value.get("canvasWidth", 0)) == int(sys.argv[2])
        and int(value.get("canvasHeight", 0)) == int(sys.argv[3])
        and float(value.get("measuredFps", 0)) > 0
        and fresh
    )
except Exception:
    ready = False
raise SystemExit(0 if ready else 1)
PY
	then
		READY=1
		break
	fi
	sleep 2
done
if [ "$READY" -ne 1 ]; then
	exit 1
fi

python3 - "$STATE_FILE" "$CURRENT_SIZE" "$SIZE" "$BACKUP" <<'PY'
import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
payload = {
    "applied_at": int(time.time()),
    "backup_file": sys.argv[4],
    "current_internal_size": sys.argv[3],
    "output_size": "1280x720",
    "previous_internal_size": sys.argv[2],
    "runtime_ready": True,
}
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
print(json.dumps(payload, sort_keys=True))
PY

ROLLBACK_ARMED=0
trap - EXIT INT TERM
