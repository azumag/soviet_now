#!/bin/bash
# monitor_webfetch_failure.sh - detect WebFetch/WebSearch failure leakage in live output artifacts.

set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 1

STATE_DIR="${TMP_STATE_DIR:-tmp/state}"
START_FILE="${WEBFETCH_MONITOR_START_FILE:-$STATE_DIR/webfetch_monitor_start_epoch}"
STATUS_FILE="${WEBFETCH_MONITOR_STATUS_FILE:-$STATE_DIR/webfetch_monitor_status.json}"
LOG_FILE="${WEBFETCH_MONITOR_LOG_FILE:-$STATE_DIR/webfetch_failure_monitor_log.jsonl}"
SINCE="${WEBFETCH_MONITOR_START_EPOCH:-}"

if [ -z "$SINCE" ] && [ -f "$START_FILE" ]; then
	SINCE=$(cat "$START_FILE" 2>/dev/null || true)
fi
case "$SINCE" in
	''|*[!0-9]*) SINCE=0 ;;
esac

pattern='webfetch failed|WebFetchの権限確認|WebFetch.*(失敗|取得できなかった|取得できません|許可|permission|denied|rejected)|WebSearch.*(失敗|取得できなかった|取得できません|許可|permission|denied|rejected)|✗[[:space:]]*(webfetch|websearch)[[:space:]]+failed'

tmp_hits=$(mktemp /tmp/soren_webfetch_monitor_hits_XXXXXXXX) || exit 1
trap 'rm -f "$tmp_hits"' EXIT

scan_file() {
	local f="$1" mt
	case "$f" in
		*_prompt.txt|*/prompt.txt|*/storage/session_diff/*|*/xdg_data/opencode/log/*) return 0 ;;
	esac
	if [ "$f" = "tmp/state/overlay_events.jsonl" ]; then
		python3 - "$f" "$SINCE" <<'PY' >>"$tmp_hits"
import json
import re
import sys

path, since_raw = sys.argv[1], sys.argv[2]
since = int(since_raw or 0)
rx = re.compile(r'webfetch failed|WebFetchの権限確認|WebFetch.*(?:失敗|取得できなかった|取得できません|許可|permission|denied|rejected)|WebSearch.*(?:失敗|取得できなかった|取得できません|許可|permission|denied|rejected)|[✗✕×]\s*(?:webfetch|websearch)\s+failed', re.I)
try:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            try:
                event = json.loads(line)
            except Exception:
                continue
            if int(event.get("ts") or 0) < since:
                continue
            text = " ".join(str(event.get(k) or "") for k in ("title", "body"))
            if rx.search(text):
                print(f"{lineno}:{line.rstrip()}")
except FileNotFoundError:
    pass
PY
		return 0
	fi
	mt=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
	[ "${mt:-0}" -ge "$SINCE" ] || return 0
	if grep -Eiq "$pattern" "$f" 2>/dev/null; then
		grep -Ein "$pattern" "$f" 2>/dev/null | head -5 >>"$tmp_hits"
	fi
}

while IFS= read -r f; do
	[ -f "$f" ] || continue
	scan_file "$f"
done < <(
	find tmp/debug tmp/.radio_deferred_queue tmp/.say_queue -type f 2>/dev/null
	[ -f tmp/state/overlay_events.jsonl ] && printf '%s\n' tmp/state/overlay_events.jsonl
)

mkdir -p "$STATE_DIR" 2>/dev/null || true
now=$(date +%s)
if [ -s "$tmp_hits" ]; then
	{
		printf '{"ok":false,"checked_at":%s,"since":%s,"hits":' "$now" "$SINCE"
		python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()[:50], ensure_ascii=False), end="")' <"$tmp_hits"
		printf '}\n'
	} >"$STATUS_FILE"
	python3 - "$LOG_FILE" "$STATUS_FILE" <<'PY'
import json
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
status_path = Path(sys.argv[2])
event = json.loads(status_path.read_text(encoding="utf-8"))
event["event"] = "webfetch_failure_detected"
with log_path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
PY
	cat "$tmp_hits"
	exit 1
fi

printf '{"ok":true,"checked_at":%s,"since":%s,"hits":[]}\n' "$now" "$SINCE" >"$STATUS_FILE"
printf 'OK no WebFetch failure leakage since %s\n' "$SINCE"
