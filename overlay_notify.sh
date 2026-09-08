#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では OBS オーバーレイ通知を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# overlay_notify.sh - append a toast event and regenerate OBS overlay HTML.
set -euo pipefail
cd "$(dirname "$0")"

ELOOP_LIB_DIR="$(pwd)"
export ELOOP_LIB_DIR
# shellcheck source=/dev/null
source "$ELOOP_LIB_DIR/core/config.sh"

category="${1:-worker}"
title="${2:-event}"
body="${3:-}"
level="${4:-info}"

mkdir -p "$(dirname "$EVENT_OVERLAY_EVENTS_FILE")" "$(dirname "$EVENT_OVERLAY_HTML_FILE")" 2>/dev/null || true

python3 - "$EVENT_OVERLAY_EVENTS_FILE" "$EVENT_OVERLAY_KEEP_EVENTS" "$category" "$title" "$body" "$level" <<'PY'
import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path

path = Path(sys.argv[1])
keep = max(1, int(sys.argv[2] or "180"))
category, title, body, level = sys.argv[3:7]
event = {
    "ts": int(time.time()),
    "category": category[:40],
    "title": title[:120],
    "body": body[:500],
    "level": level[:20],
}
# Shared with docich.overlay_queue: the lock follows the events path,
# including custom paths and aliases of its parent directory. Never unlink it.
path.parent.mkdir(parents=True, exist_ok=True)
lock_path = path.parent.resolve() / (path.name + ".lock")
with lock_path.open("a") as lock:
    deadline = time.monotonic() + 5.0
    while True:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise SystemExit("another overlay edit in progress")
            time.sleep(0.01)
    lines = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        pass
    lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    lines = [line for line in lines if line.strip()][-keep:]
    fd, tmp = tempfile.mkstemp(prefix=".overlay_events.", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

PY

EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR" \
EVENT_OVERLAY_COMMENT_GEN_STATE="$COMMENT_GEN_STATE_FILE" \
EVENT_OVERLAY_RADIO_STATE="$RADIO_STATE_FILE" \
python3 "$ELOOP_LIB_DIR/generate_event_overlay.py" \
	"$EVENT_OVERLAY_EVENTS_FILE" \
	"$EVENT_OVERLAY_HTML_FILE" \
	"$EVENT_OVERLAY_KEEP_EVENTS" \
	"$EVENT_OVERLAY_VISIBLE_SEC" \
	"$CODEX_WORK_OVERLAY_STATE_FILE"

./obs_control.sh stack "${OBS_DASHBOARD_SCENE:-soren}" >/dev/null 2>&1 || true

if [ "${OVERLAY_NOTIFY_OBS_SHOW:-0}" = "1" ]; then
	./obs_control.sh show "${OBS_DASHBOARD_SCENE:-soren}" "$EVENT_OVERLAY_SOURCE" >/dev/null 2>&1 || true
fi
