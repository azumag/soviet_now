#!/bin/bash
set -euo pipefail

ELOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ELOOP_LIB_DIR
# shellcheck source=/dev/null
source "$ELOOP_LIB_DIR/core/config.sh"

mode="${1:-once}"
watch_pid="${2:-}"

generate_once() {
	mkdir -p "$(dirname "$IMPROVE_OVERLAY_HTML_FILE")" "$(dirname "$IMPROVE_OVERLAY_LOG_FILE")" 2>/dev/null || true
	python3 - "$IMPROVE_STATE_FILE" "$IMPROVE_AI_LOG_FILE" "$IMPROVE_OVERLAY_HTML_FILE" "$IMPROVE_OVERLAY_REFRESH_SEC" <<'PY'
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime

state_path, log_path, out_path, refresh = sys.argv[1:5]
ansi_re = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

def read_state():
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def read_lines(limit=24):
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        lines = []
    return [ansi_re.sub("", line.rstrip("\n")) for line in lines if line.rstrip("\n")]

state = read_state()
lines = read_lines()
status = str(state.get("status") or "idle")
phase = str(state.get("phase") or "-")
detail = str(state.get("detail") or "")
pid = str(state.get("pid") or 0)
progress = str(state.get("progress") or 0)
updated_at = state.get("updated_at") or 0
try:
    updated_text = datetime.fromtimestamp(int(updated_at)).strftime("%H:%M:%S") if int(updated_at) else "-"
except Exception:
    updated_text = "-"

body_lines = "\n".join(f"<div>{html.escape(line)}</div>" for line in lines[-24:])
if not body_lines:
    body_lines = '<div class="muted">waiting for improve log...</div>'

doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{html.escape(str(refresh))}">
<style>
:root {{
  color-scheme: dark;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
}}
.wrap {{
  box-sizing: border-box;
  width: 100vw;
  height: 100vh;
  padding: 18px 20px;
  color: #eaf2ff;
  background: rgba(4, 8, 14, 0.82);
  border-left: 6px solid #58d68d;
}}
.head {{
  display: flex;
  align-items: baseline;
  gap: 16px;
  margin-bottom: 14px;
  white-space: nowrap;
}}
.title {{
  font-size: 34px;
  font-weight: 800;
  letter-spacing: 0;
}}
.pill {{
  padding: 5px 12px;
  border: 1px solid rgba(234, 242, 255, 0.35);
  border-radius: 5px;
  font-size: 22px;
  color: #d7e7ff;
}}
.meta {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-bottom: 16px;
  font-size: 21px;
  color: #c7d8ef;
}}
.detail {{
  flex-basis: 100%;
  color: #f2d27c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.log {{
  height: calc(100vh - 150px);
  overflow: hidden;
  font-size: 20px;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-word;
}}
.log div:nth-last-child(-n+8) {{
  color: #ffffff;
}}
.muted {{
  color: rgba(234, 242, 255, 0.55);
}}
</style>
</head>
<body>
<main class="wrap">
  <div class="head">
    <div class="title">IMPROVEMENT DAEMON</div>
    <div class="pill">{html.escape(status)}</div>
  </div>
  <div class="meta">
    <div>phase: {html.escape(phase)}</div>
    <div>progress: {html.escape(progress)}%</div>
    <div>pid: {html.escape(pid)}</div>
    <div>updated: {html.escape(updated_text)}</div>
    <div>refresh: {html.escape(str(refresh))}s</div>
    <div class="detail">{html.escape(detail)}</div>
  </div>
  <section class="log">{body_lines}</section>
</main>
</body>
</html>
"""

out_dir = os.path.dirname(out_path)
os.makedirs(out_dir, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".improve_overlay.", suffix=".html", dir=out_dir)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(doc)
os.replace(tmp, out_path)
try:
    os.chmod(out_path, 0o644)
except OSError:
    pass
PY
}

case "$mode" in
once)
	generate_once
	;;
watch)
	while true; do
		generate_once >>"$IMPROVE_OVERLAY_LOG_FILE" 2>&1 || true
		if [ -n "$watch_pid" ] && ! kill -0 "$watch_pid" 2>/dev/null; then
			generate_once >>"$IMPROVE_OVERLAY_LOG_FILE" 2>&1 || true
			exit 0
		fi
		sleep "$IMPROVE_OVERLAY_REFRESH_SEC"
	done
	;;
*)
	echo "usage: $0 [once|watch [pid]]" >&2
	exit 2
	;;
esac
