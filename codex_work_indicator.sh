#!/bin/bash
# codex_work_indicator.sh - keep the Codex work indicator inside eventOverlay.
set -euo pipefail
cd "$(dirname "$0")"

ELOOP_LIB_DIR="$(pwd)"
export ELOOP_LIB_DIR
# shellcheck source=/dev/null
source "$ELOOP_LIB_DIR/core/config.sh"

action="${1:-start}"
title="${2:-システム自動分析・修正作業中}"
body="${3:-メリケンAI が確認・修正・検証を進めています}"

mkdir -p "$(dirname "$CODEX_WORK_OVERLAY_STATE_FILE")" "$(dirname "$EVENT_OVERLAY_HTML_FILE")"

case "$action" in
start|show|on)
	python3 - "$CODEX_WORK_OVERLAY_STATE_FILE" "$title" "$body" <<'PY'
import json
import os
import sys
import tempfile
import time
from pathlib import Path

path = Path(sys.argv[1])
state = {
    "active": True,
    "ts": int(time.time()),
    "title": sys.argv[2][:80],
    "body": sys.argv[3][:240],
}
fd, tmp = tempfile.mkstemp(prefix=".codex_work.", suffix=".json", dir=str(path.parent))
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    f.write("\n")
os.replace(tmp, path)
PY
	;;
stop|hide|off|clear)
	rm -f "$CODEX_WORK_OVERLAY_STATE_FILE"
	;;
status)
	if [ -s "$CODEX_WORK_OVERLAY_STATE_FILE" ]; then
		cat "$CODEX_WORK_OVERLAY_STATE_FILE"
	else
		echo '{"active":false}'
	fi
	exit 0
	;;
*)
	echo "usage: $0 start|stop|status [title] [body]" >&2
	exit 2
	;;
esac

python3 "$ELOOP_LIB_DIR/generate_event_overlay.py" \
	"$EVENT_OVERLAY_EVENTS_FILE" \
	"$EVENT_OVERLAY_HTML_FILE" \
	"$EVENT_OVERLAY_KEEP_EVENTS" \
	"$EVENT_OVERLAY_VISIBLE_SEC" \
	"$CODEX_WORK_OVERLAY_STATE_FILE"

./obs_control.sh stack "${OBS_DASHBOARD_SCENE:-soren}" >/dev/null 2>&1 || true
