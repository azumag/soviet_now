#!/bin/bash
# generate_status_overlay.sh - render show_status_g/status_dashboard as an OBS HTML overlay.
#
# Usage:
#   ./generate_status_overlay.sh once
#   ./generate_status_overlay.sh watch [interval_sec]
#   ./generate_status_overlay.sh start [interval_sec]
#   ./generate_status_overlay.sh stop
#   ./generate_status_overlay.sh ensure-obs [show|hide]

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

# shellcheck source=/dev/null
source ./eloop_lib.sh

mode="${1:-once}"
interval="${2:-2}"
case "$interval" in
''|*[!0-9]*) interval=2 ;;
esac
(( interval < 1 )) && interval=1

out_file="${STATUS_OVERLAY_HTML_FILE:-tmp/state/status_overlay.html}"
width="${STATUS_OVERLAY_WIDTH:-560}"
height="${STATUS_OVERLAY_HEIGHT:-820}"
pid_file="tmp/state/status_overlay_watch.pid"
log_file="tmp/debug/status_overlay.log"
tmux_session="soren_status_overlay"

render_once() {
	mkdir -p "$(dirname "$out_file")"
	local raw=""
	raw=$(HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=1 python3 status_dashboard.py 2>/dev/null || true)
	STATUS_OVERLAY_RAW="$raw" python3 - "$out_file" "$width" "$height" <<'PY'
import html
import os
import re
import sys
import tempfile
import time

out_file, width, height = sys.argv[1:4]
raw = os.environ.get("STATUS_OVERLAY_RAW", "")
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

body = ansi_to_html(raw.rstrip() or "status_dashboard.py returned no output")
generated = time.strftime("%H:%M:%S")
doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{max(1, int(os.environ.get('STATUS_OVERLAY_REFRESH_SEC', '2')))}">
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
  padding: 10px 10px 8px;
  color: #e5f7ff;
  background: linear-gradient(180deg, rgba(2, 8, 23, .92), rgba(3, 7, 18, .88));
  border: 1px solid rgba(56, 189, 248, .28);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
}}
.meta {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 5px;
  font: 700 13px/1.15 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
  font: 15.5px/1.13 "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  letter-spacing: 0;
  color: #dbeafe;
}}
</style>
</head>
<body>
<div class="frame">
  <div class="meta"><span>SOREN STATS</span><span>{html.escape(generated)}</span></div>
  <pre>{body}</pre>
</div>
</body>
</html>
"""
os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".status_overlay.", suffix=".html", dir=os.path.dirname(out_file) or ".")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(doc)
os.replace(tmp, out_file)
PY
	chmod 644 "$out_file" 2>/dev/null || true
	printf 'generated:%s\n' "$out_file"
}

apply_obs_transform() {
	local scene="${OBS_DASHBOARD_SCENE:-soren}"
	local source="${STATUS_OVERLAY_SOURCE:-statsOverlay}"
	local x="${STATUS_OVERLAY_OBS_X:-24}"
	local y="${STATUS_OVERLAY_OBS_Y:-255}"
	local scale_x="${STATUS_OVERLAY_OBS_SCALE_X:-0.90}"
	local scale_y="${STATUS_OVERLAY_OBS_SCALE_Y:-0.74}"

	[ -n "${OBS_WEBSOCKET_PORT:-}" ] || return 0
	[ -n "${OBS_WEBSOCKET_PASSWORD:-}" ] || return 0

	node - "$scene" "$source" "$x" "$y" "$scale_x" "$scale_y" <<'NODE'
const crypto = require('crypto');

const [sceneName, sourceName, xRaw, yRaw, scaleXRaw, scaleYRaw] = process.argv.slice(2);
const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
const port = Number(process.env.OBS_WEBSOCKET_PORT || 4455);
const password = process.env.OBS_WEBSOCKET_PASSWORD || '';
const requestTimeoutMs = Number(process.env.OBS_WEBSOCKET_TIMEOUT_MS || 8000);
const url = `ws://${host}:${port}`;

function fail(message, code = 1) {
  console.error(`[status_overlay_transform] ${message}`);
  process.exit(code);
}

if (typeof WebSocket !== 'function') fail('Global WebSocket is not available in this Node.js runtime');

function sha256Base64(text) {
  return crypto.createHash('sha256').update(text).digest('base64');
}

async function connectAndIdentify() {
  const ws = new WebSocket(url);
  const state = { ws, requestSeq: 0, hello: null, ready: false, pending: new Map() };
  const cleanupPending = (error) => {
    for (const { reject, timer } of state.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    state.pending.clear();
  };

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Timed out connecting to ${url}`)), requestTimeoutMs);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve();
    });
    ws.addEventListener('error', (event) => {
      clearTimeout(timer);
      reject(new Error(event && event.error && event.error.message ? event.error.message : `Failed to connect to ${url}`));
    });
  });

  ws.addEventListener('message', (event) => {
    let payload;
    try {
      payload = JSON.parse(String(event.data));
    } catch (err) {
      cleanupPending(err);
      return;
    }
    if (payload.op === 0) {
      state.hello = payload.d || {};
      return;
    }
    if (payload.op === 2) {
      state.ready = true;
      return;
    }
    if (payload.op === 7) {
      const data = payload.d || {};
      const requestId = data.requestId;
      if (!requestId || !state.pending.has(requestId)) return;
      const pending = state.pending.get(requestId);
      state.pending.delete(requestId);
      clearTimeout(pending.timer);
      const status = data.requestStatus || {};
      if (status.result) pending.resolve(data.responseData || {});
      else pending.reject(new Error(`${data.requestType} failed (${status.code}): ${status.comment || 'unknown error'}`));
    }
  });

  const deadline = Date.now() + requestTimeoutMs;
  while (!state.hello) {
    if (Date.now() > deadline) throw new Error('Timed out waiting for OBS Hello');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  const identify = { op: 1, d: { rpcVersion: 1, eventSubscriptions: 0 } };
  const auth = state.hello.authentication;
  if (auth && auth.challenge && auth.salt) {
    const secret = sha256Base64(password + auth.salt);
    identify.d.authentication = sha256Base64(secret + auth.challenge);
  }
  ws.send(JSON.stringify(identify));

  const readyDeadline = Date.now() + requestTimeoutMs;
  while (!state.ready) {
    if (Date.now() > readyDeadline) throw new Error('Timed out waiting for OBS Identify');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  state.request = (requestType, requestData = {}) => {
    const requestId = `req-${++state.requestSeq}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        state.pending.delete(requestId);
        reject(new Error(`${requestType} timed out`));
      }, requestTimeoutMs);
      state.pending.set(requestId, { resolve, reject, timer });
      ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
    });
  };
  state.close = async () => {
    cleanupPending(new Error('OBS connection closed'));
    try { ws.close(); } catch (_) {}
  };
  return state;
}

async function main() {
  const obs = await connectAndIdentify();
  try {
    const list = await obs.request('GetSceneItemList', { sceneName });
    const item = (list.sceneItems || []).find(it => it.sourceName === sourceName);
    if (!item) throw new Error(`source not found: ${sourceName}`);
    await obs.request('SetSceneItemTransform', {
      sceneName,
      sceneItemId: item.sceneItemId,
      sceneItemTransform: {
        positionX: Number(xRaw),
        positionY: Number(yRaw),
        rotation: 0,
        scaleX: Number(scaleXRaw),
        scaleY: Number(scaleYRaw),
        cropLeft: 0,
        cropTop: 0,
        cropRight: 0,
        cropBottom: 0,
        alignment: 5,
        boundsType: 'OBS_BOUNDS_NONE',
      },
    });
    console.log(`transformed:${sourceName}:x=${xRaw}:y=${yRaw}:sx=${scaleXRaw}:sy=${scaleYRaw}`);
  } finally {
    await obs.close();
  }
}

main().catch(err => fail(err && err.message ? err.message : String(err)));
NODE
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
		tmux new-session -d -s "$tmux_session" "cd '$PWD' && exec ./generate_status_overlay.sh watch '$interval' >> '$log_file' 2>&1"
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
	./obs_browser_source.sh ensure "${OBS_DASHBOARD_SCENE:-soren}" "${STATUS_OVERLAY_SOURCE:-statsOverlay}" "$out_file" "$width" "$height" "$visibility"
	if [ "$visibility" = "show" ] && [ "${STATUS_OVERLAY_OBS_TRANSFORM_ENABLED:-0}" = "force" ]; then
		apply_obs_transform || true
	fi
	;;
*)
	echo "usage: $0 once|watch [interval_sec]|start [interval_sec]|stop|ensure-obs [show|hide]" >&2
	exit 2
	;;
esac
