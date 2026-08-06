#!/bin/bash
# 探索モード (EXPLORE_MODE=1) ではステータスオーバーレイ生成を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# generate_show_status_overlay.sh - render show_status.sh as an OBS HTML overlay.
#
# Usage:
#   ./generate_show_status_overlay.sh once
#   ./generate_show_status_overlay.sh watch [interval_sec]
#   ./generate_show_status_overlay.sh start [interval_sec]
#   ./generate_show_status_overlay.sh stop
#   ./generate_show_status_overlay.sh ensure-obs [show|hide]

set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=/dev/null
source ./eloop_lib.sh

mode="${1:-once}"
interval="${2:-2}"
case "$interval" in
''|*[!0-9]*) interval=2 ;;
esac
(( interval < 1 )) && interval=1

out_file="${SHOW_STATUS_OVERLAY_HTML_FILE:-tmp/state/show_status_overlay.html}"
width="${SHOW_STATUS_OVERLAY_WIDTH:-520}"
height="${SHOW_STATUS_OVERLAY_HEIGHT:-980}"
pid_file="tmp/state/show_status_overlay_watch.pid"
log_file="tmp/debug/show_status_overlay.log"
tmux_session="soren_show_status_overlay"

render_once() {
	mkdir -p "$(dirname "$out_file")"
	local show_raw=""
	local raw=""
	show_raw=$(SHOW_STATUS_NO_FLICKER=1 ./show_status.sh --once 2>/dev/null || true)
	raw="$show_raw"
	SHOW_STATUS_OVERLAY_RAW="$raw" SHOW_STATUS_RAW="$show_raw" HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=1 python3 - "$out_file" "$width" "$height" <<'PY'
import html
import os
import re
import sys
import tempfile
import time

out_file, width, height = sys.argv[1:4]
raw = os.environ.get("SHOW_STATUS_OVERLAY_RAW", "")
csi_re = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
palette = {
    "0": "",
    "1": "font-weight:700",
    "2": "opacity:.68",
    "31": "color:#ef4444",
    "32": "color:#22c55e",
    "33": "color:#facc15",
    "34": "color:#38bdf8",
    "35": "color:#c084fc",
    "36": "color:#22d3ee",
    "37": "color:#f8fafc",
    "90": "color:#94a3b8",
    "91": "color:#f87171",
    "92": "color:#4ade80",
    "93": "color:#fde047",
    "94": "color:#60a5fa",
    "95": "color:#e879f9",
    "96": "color:#67e8f9",
    "97": "color:#ffffff",
    "38;5;33": "color:#38bdf8",
    "38;5;34": "color:#22c55e",
    "38;5;37": "color:#22d3ee",
    "38;5;46": "color:#4ade80",
    "38;5;82": "color:#22c55e",
    "38;5;118": "color:#86efac",
    "38;5;154": "color:#a3e635",
    "38;5;196": "color:#ef4444",
    "38;5;202": "color:#f97316",
    "38;5;208": "color:#fb923c",
    "38;5;214": "color:#f59e0b",
    "38;5;220": "color:#facc15",
    "38;5;226": "color:#fde047",
    "38;5;245": "color:#94a3b8",
    "38;5;255": "color:#f8fafc",
}

def ansi_to_html(text):
    out = []
    stack = []
    pos = 0
    for match in csi_re.finditer(text):
        out.append(html.escape(text[pos:match.start()]))
        final = match.group(3)
        code = match.group(1) or "0"
        if final != "m":
            pos = match.end()
            continue
        if code == "0":
            while stack:
                out.append("</span>")
                stack.pop()
        else:
            style = palette.get(code)
            if style:
                out.append(f'<span style="{style}">')
                stack.append("span")
        pos = match.end()
    out.append(html.escape(text[pos:]))
    while stack:
        out.append("</span>")
        stack.pop()
    return "".join(out)

body = ansi_to_html(raw.rstrip() or "show_status.sh returned no output")
generated = time.strftime("%H:%M:%S")
doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{max(1, int(os.environ.get('SHOW_STATUS_OVERLAY_REFRESH_SEC', '2')))}">
<style>
html, body {{
  margin: 0;
  width: {html.escape(width)}px;
  height: {html.escape(height)}px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0);
}}
.frame {{
  box-sizing: border-box;
  width: {html.escape(width)}px;
  height: {html.escape(height)}px;
  padding: 14px 14px 12px;
  color: #e5f7ff;
  background: linear-gradient(180deg, rgba(2, 8, 23, .92), rgba(3, 7, 18, .88));
  border: 1px solid rgba(125, 211, 252, .30);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
}}
.meta {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font: 700 14px/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #bae6fd;
  letter-spacing: 0;
}}
.meta span:last-child {{
  color: #94a3b8;
  font-weight: 600;
}}
pre {{
  margin: 0;
  white-space: pre;
  font: 12.8px/1.17 "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  letter-spacing: 0;
  color: #dbeafe;
}}
</style>
</head>
<body>
<div class="frame">
  <div class="meta"><span>SOREN OPS</span><span>{html.escape(generated)}</span></div>
  <pre>{body}</pre>
</div>
</body>
</html>
"""
os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".show_status_overlay.", suffix=".html", dir=os.path.dirname(out_file) or ".")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(doc)
os.replace(tmp, out_file)
PY
	chmod 644 "$out_file" 2>/dev/null || true
	# eventOverlay の生成中インジケーター (コメント/ラジオ) を同じ ~2s tick で再描画する。
	# 状態ファイル (.comment_gen_state / .radio_state) の鮮度を読み、生成が続く間だけ
	# スピナーを表示し、シグナルが消える/期限切れになると自動的に消える。
	render_event_overlay_indicators || true
	printf 'generated:%s\n' "$out_file"
}

render_event_overlay_indicators() {
	[ -f "$ELOOP_LIB_DIR/generate_event_overlay.py" ] || return 0
	EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR" \
	EVENT_OVERLAY_COMMENT_GEN_STATE="${COMMENT_GEN_STATE_FILE:-tmp/state/.comment_gen_state}" \
	EVENT_OVERLAY_RADIO_STATE="${RADIO_STATE_FILE:-tmp/state/.radio_state}" \
	python3 "$ELOOP_LIB_DIR/generate_event_overlay.py" \
		"$EVENT_OVERLAY_EVENTS_FILE" \
		"$EVENT_OVERLAY_HTML_FILE" \
		"$EVENT_OVERLAY_KEEP_EVENTS" \
		"$EVENT_OVERLAY_VISIBLE_SEC" \
		"$CODEX_WORK_OVERLAY_STATE_FILE" >/dev/null 2>&1 || true
}

case "$mode" in
once)
	render_once
	;;
watch)
	while true; do
		render_once >/dev/null 2>&1 || true
		sleep "$interval"
	done
	;;
start)
	mkdir -p "$(dirname "$pid_file")"
	mkdir -p "$(dirname "$log_file")"
	if command -v tmux >/dev/null 2>&1; then
		if tmux has-session -t "$tmux_session" 2>/dev/null; then
			old_pid=$(tmux display-message -p -t "$tmux_session" "#{pane_pid}" 2>/dev/null || true)
			printf '%s\n' "$old_pid" >"$pid_file"
			printf 'running:%s\n' "$old_pid"
			exit 0
		fi
		tmux new-session -d -s "$tmux_session" "cd '$PWD' && exec ./generate_show_status_overlay.sh watch '$interval' >> '$log_file' 2>&1"
		sleep 1
		if tmux has-session -t "$tmux_session" 2>/dev/null; then
			new_pid=$(tmux display-message -p -t "$tmux_session" "#{pane_pid}" 2>/dev/null || true)
			printf '%s\n' "$new_pid" >"$pid_file"
			printf 'started:%s\n' "$new_pid"
			exit 0
		fi
		printf 'tmux-start-failed:fallback-nohup\n' >>"$log_file"
	fi
	if [ -f "$pid_file" ]; then
		old_pid=$(cat "$pid_file" 2>/dev/null || true)
		case "$old_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$old_pid" 2>/dev/null; then
				printf 'running:%s\n' "$old_pid"
				exit 0
			fi
			;;
		esac
	fi
	nohup "$0" watch "$interval" >"$log_file" 2>&1 &
	new_pid=$!
	printf '%s\n' "$new_pid" >"$pid_file"
	printf 'started:%s\n' "$new_pid"
	;;
stop)
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t "$tmux_session" 2>/dev/null || true
	fi
	if [ -f "$pid_file" ]; then
		old_pid=$(cat "$pid_file" 2>/dev/null || true)
		case "$old_pid" in
		''|*[!0-9]*) ;;
		*) kill "$old_pid" 2>/dev/null || true ;;
		esac
		rm -f "$pid_file"
	fi
	printf 'stopped\n'
	;;
ensure-obs)
	render_once >/dev/null
	visibility="${2:-show}"
	case "$visibility" in show|hide) ;; *) visibility=show ;; esac
	./obs_browser_source.sh ensure "${OBS_DASHBOARD_SCENE:-soren}" "${SHOW_STATUS_OVERLAY_SOURCE:-opsOverlay}" "$out_file" "$width" "$height" "$visibility"
	;;
*)
	echo "usage: $0 once|watch [interval_sec]|start [interval_sec]|stop|ensure-obs [show|hide]" >&2
	exit 2
	;;
esac
