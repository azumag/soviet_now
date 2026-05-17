#!/bin/bash
# viewer_chat_monitor.sh - summarize recent viewer chat for monitoring surfaces.

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a
# shellcheck source=/dev/null
source ./eloop_lib.sh

mode="${1:-line}"

python3 - \
	"${VIEWER_CHAT_MONITOR_SOURCE:-tmp/.twitch_chat/comment_context_history.log}" \
	"${VIEWER_CHAT_MONITOR_FILE:-tmp/state/viewer_chat_monitor.json}" \
	"${VIEWER_CHAT_MONITOR_LOOKBACK:-200}" \
	"$mode" <<'PY'
import json
import os
import re
import sys
import time

source_file, out_file, lookback_raw, mode = sys.argv[1:5]

def as_int(value, default):
    try:
        return int(value)
    except Exception:
        return default

def is_observer_comment(line):
    text = line.strip()
    if not text:
        return False
    low = text.lower()
    if low.startswith(("streamelements:", "nightbot:")):
        return False
    # Card gacha/result lines dominate comment history but are operational
    # noise for strategy monitoring.
    if text.startswith("@") and ("獲得しました" in text or "連ガチャ" in text):
        return False
    if "Twitchエモート:" in text or "種中" in text:
        return False
    if re.search(r"https?://", text):
        return False
    if ":" not in text:
        return False
    message = text.split(":", 1)[1].strip()
    if len(message) < 2:
        return False
    if looks_like_emote_only(message):
        return False
    return True

def looks_like_emote_only(message):
    parts = message.split()
    if len(parts) != 1:
        return False
    token = parts[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,40}", token):
        return False
    return bool(re.search(r"[a-z][A-Z]", token) or token.lower().startswith(("azumag", "unagee")))

def compact(line):
    return re.sub(r"\s+", " ", line.strip())[:96]

lookback = max(20, as_int(lookback_raw, 200))
try:
    with open(source_file, encoding="utf-8", errors="ignore") as f:
        raw_lines = f.read().splitlines()
except Exception:
    raw_lines = []

lines = [compact(raw) for raw in raw_lines[-lookback:] if is_observer_comment(raw)]
recent = lines[-3:]
latest = recent[-1] if recent else ""
payload = {
    "epoch": int(time.time()),
    "source": source_file,
    "latest": latest,
    "recent": recent,
    "count": len(lines),
}
os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
tmp = out_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
os.replace(tmp, out_file)

if mode == "json":
    print(json.dumps(payload, ensure_ascii=False))
elif latest:
    print(latest)
else:
    print("none")
PY
