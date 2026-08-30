#!/bin/bash
# generate_soren_overlay.sh - unified SOREN overlay (OPS + STATS in one HTML)
#
# 統合 1 画面: show_status (OPS) と status_dashboard (STATS) を 1 枚に統合し、
# 重複項目を片側へ寄せて表示する。旧 2 枚 (opsOverlay / statsOverlay) の置換。
#
# Usage:
#   ./generate_soren_overlay.sh once
#   ./generate_soren_overlay.sh watch [interval_sec]
#   ./generate_soren_overlay.sh start [interval_sec]
#   ./generate_soren_overlay.sh stop
#   ./generate_soren_overlay.sh ensure-obs [show|hide]

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

out_file="${SOREN_OVERLAY_HTML_FILE:-tmp/state/soren_overlay.html}"
width="${SOREN_OVERLAY_WIDTH:-1120}"
height="${SOREN_OVERLAY_HEIGHT:-980}"
pid_file="tmp/state/soren_overlay_watch.pid"
log_file="tmp/debug/soren_overlay.log"
tmux_session="soren_soren_overlay"

render_once() {
	mkdir -p "$(dirname "$out_file")"
	local ops_raw="" stats_raw=""
	ops_raw=$(SHOW_STATUS_NO_FLICKER=1 ./show_status.sh --once 2>/dev/null || true)
	stats_raw=$(HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=0 python3 status_dashboard.py 2>/dev/null || true)

	SOREN_OPS_RAW="$ops_raw" SOREN_STATS_RAW="$stats_raw" python3 - "$out_file" "$width" "$height" <<'PY'
import html
import os
import re
import sys
import tempfile
import time

from lib.overlay_text import normalize_overlay_text

out_file, width, height = sys.argv[1:4]
ops_raw = normalize_overlay_text(os.environ.get("SOREN_OPS_RAW", ""))
stats_raw = normalize_overlay_text(os.environ.get("SOREN_STATS_RAW", ""))

csi_re = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
ansi_strip_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# unified palette = union(OPS + STATS)
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
    "38;5;190": "color:#bef264",
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

def filter_ops_for_unified(raw: str) -> str:
    """Remove lines that are canonically shown in STATS to avoid duplication.
    対象は show_status.sh 側の重複詳細行。STATS側が正とされる項目:
      - LastDrop (STATS headerが正)
      - AI 429 (STATS Observerが正, per-role)
      - ChatObs / ImproveBackoff / S91Improve / ArchiveNext / WildPar* / AnnealObs
    Safety.rejected や ROLLBACKS全体はOPSの詳細として残すが、ヘッダのRejected重複は小さいため残す。
    """
    dup_keywords = [
        "LastDrop",      # -> STATS Header LastDrop
        "AI 429",        # -> STATS AI 429
        "ChatObs",       # -> STATS Observer ChatObs
        "ImproveBack",   # -> STATS Observer ImproveBackoff
        "S91Improve",    # -> STATS Observer S91Improve
        "ArchiveNext",   # -> STATS ArchiveRestart candidates
        "WildPar",       # WildParFail / WildParallel
        "WildEval",      # WildEval (Escape詳細)
        "AnnealObs",     # -> STATS AnnealObs
    ]
    out_lines = []
    for line in raw.splitlines():
        plain = ansi_strip_re.sub("", line)
        # filter if any dup keyword appears
        if any(kw in plain for kw in dup_keywords):
            continue
        out_lines.append(line)
    # Also strip empty CONSEcutive duplicate headers left behind?
    return "\n".join(out_lines)

def filter_stats_for_unified(raw: str) -> str:
    """STATS側は原則そのまま。OPS詳細が勝つ Improve 短縮行だけ除外する場合はここで．
    現状は重複が小さいため無フィルタにし、OPS詳細と並存させる。
    必要ならヘッダの 'Imp:' 短縮行を除くロジックを入れる。
    """
    return raw

filtered_ops = filter_ops_for_unified(ops_raw)
filtered_stats = filter_stats_for_unified(stats_raw)

# fallback messages
if not filtered_ops.strip():
    filtered_ops = "ops: no output (show_status.sh --once returned empty)"
if not filtered_stats.strip():
    filtered_stats = "stats: no output (status_dashboard.py returned empty)"

html_ops = ansi_to_html(filtered_ops.rstrip())
html_stats = ansi_to_html(filtered_stats.rstrip())
generated = time.strftime("%H:%M:%S")
refresh = max(1, int(os.environ.get("SOREN_OVERLAY_REFRESH_SEC", "2") or 2))
# width/height are numeric strings; escape for safety
w_esc = html.escape(width)
h_esc = html.escape(height)

doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<style>
html, body {{
  margin: 0;
  width: {w_esc}px;
  height: {h_esc}px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0);
}}
.frame {{
  box-sizing: border-box;
  width: {w_esc}px;
  height: {h_esc}px;
  padding: 10px 10px 8px;
  color: #e5f7ff;
  background: linear-gradient(180deg, rgba(2, 8, 23, .92), rgba(3, 7, 18, .88));
  border: 1px solid rgba(125, 211, 252, .30);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
  display: flex;
  flex-direction: column;
  gap: 8px;
}}
.meta {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  font: 700 14px/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #bae6fd;
  letter-spacing: 0;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(125,211,252,.18);
}}
.meta span:last-child {{
  color: #94a3b8;
  font-weight: 600;
}}
.grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  flex: 1;
  min-height: 0;
}}
.panel {{
  box-sizing: border-box;
  border: 1px solid rgba(125,211,252,.18);
  border-radius: 6px;
  background: rgba(255,255,255,.02);
  padding: 8px 8px 6px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}}
.panel-title {{
  font: 700 12px/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #7dd3fc;
  letter-spacing: .04em;
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid rgba(125,211,252,.12);
}}
.panel.ops .panel-title {{ color: #facc15; border-bottom-color: rgba(250,204,21,.18); }}
.panel.stats .panel-title {{ color: #7dd3fc; }}
.panel pre {{
  margin: 0;
  white-space: pre;
  font: 11.5px/1.16 "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  letter-spacing: 0;
  color: #dbeafe;
  overflow: hidden;
  flex: 1;
}}
.hint {{
  font: 600 10px/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #94a3b8;
  text-align: center;
  padding-top: 4px;
  opacity: .7;
}}
</style>
</head>
<body>
<div class="frame">
  <div class="meta"><span>SOREN UNIFIED</span><span>{html.escape(generated)} · OPS + STATS 統合 (重複は STATS へ寄せ)</span></div>
  <div class="grid">
    <div class="panel ops"><div class="panel-title">OPS — show_status (重複除去済)</div><pre>{html_ops}</pre></div>
    <div class="panel stats"><div class="panel-title">STATS — status_dashboard</div><pre>{html_stats}</pre></div>
  </div>
  <div class="hint">LastDrop / AI 429 / ChatObs / ImproveBackoff / S91 / ArchiveNext / Wild* / Anneal は STATS 側に一本化 · OPSは Worker/Audio/Queue/Improve詳細を担当</div>
</div>
</body>
</html>
"""
os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".soren_overlay.", suffix=".html", dir=os.path.dirname(out_file) or ".")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(doc)
os.replace(tmp, out_file)

# ── 旧2ファイルもフィルタ済みで更新して broadcast 経由の配信にも反映する ──
# show_status_overlay.html (OPS) はフィルタ済みOPS、status_overlay.html (STATS) はフィルタ済みSTATS
try:
    ops_legacy_file = os.environ.get("SHOW_STATUS_OVERLAY_HTML_FILE", "tmp/state/show_status_overlay.html")
    stats_legacy_file = os.environ.get("STATUS_OVERLAY_HTML_FILE", "tmp/state/status_overlay.html")
    # OPS legacy: 520x680, title SOREN OPS
    ops_body = ansi_to_html(filtered_ops.rstrip() or "show_status.sh returned no output")
    ops_doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<style>
html, body {{ margin: 0; width: 520px; height: 680px; overflow: hidden; background: rgba(0, 0, 0, 0); }}
.frame {{ box-sizing: border-box; width: 520px; height: 680px; padding: 14px 14px 12px; color: #e5f7ff; background: linear-gradient(180deg, rgba(2, 8, 23, .92), rgba(3, 7, 18, .88)); border: 1px solid rgba(125, 211, 252, .30); border-radius: 8px; box-shadow: inset 0 0 0 1px rgba(255,255,255,.04); }}
.meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font: 700 14px/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #bae6fd; }}
.meta span:last-child {{ color: #94a3b8; font-weight: 600; }}
pre {{ margin: 0; white-space: pre; font: 12.8px/1.17 "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; color: #dbeafe; }}
</style>
</head>
<body>
<div class="frame">
  <div class="meta"><span>SOREN OPS</span><span>{html.escape(generated)}</span></div>
  <pre>{ops_body}</pre>
</div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(ops_legacy_file) or ".", exist_ok=True)
    fd2, tmp2 = tempfile.mkstemp(prefix=".show_status_overlay.", suffix=".html", dir=os.path.dirname(ops_legacy_file) or ".")
    with os.fdopen(fd2, "w", encoding="utf-8") as f:
        f.write(ops_doc)
    os.replace(tmp2, ops_legacy_file)

    stats_body = ansi_to_html(filtered_stats.rstrip() or "status_dashboard.py returned no output")
    stats_doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<style>
html, body {{ margin: 0; width: 560px; height: 820px; overflow: hidden; background: rgba(0, 0, 0, 0); }}
.frame {{ box-sizing: border-box; width: 560px; height: 820px; padding: 10px 10px 8px; color: #e5f7ff; background: linear-gradient(180deg, rgba(2, 8, 23, .92), rgba(3, 7, 18, .88)); border: 1px solid rgba(56, 189, 248, .28); border-radius: 8px; box-shadow: inset 0 0 0 1px rgba(255,255,255,.04); }}
.meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; font: 700 13px/1.15 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #bae6fd; }}
.meta span:last-child {{ color: #94a3b8; font-weight: 600; }}
pre {{ margin: 0; white-space: pre; font: 15.5px/1.13 "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; color: #dbeafe; }}
</style>
</head>
<body>
<div class="frame">
  <div class="meta"><span>SOREN STATS</span><span>{html.escape(generated)}</span></div>
  <pre>{stats_body}</pre>
</div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(stats_legacy_file) or ".", exist_ok=True)
    fd3, tmp3 = tempfile.mkstemp(prefix=".status_overlay.", suffix=".html", dir=os.path.dirname(stats_legacy_file) or ".")
    with os.fdopen(fd3, "w", encoding="utf-8") as f:
        f.write(stats_doc)
    os.replace(tmp3, stats_legacy_file)
except Exception as e:
    # legacy更新失敗は致命ではない、soren本体は成功しているので握りつぶす
    pass
PY
	chmod 644 "$out_file" 2>/dev/null || true
	chmod 644 "${SHOW_STATUS_OVERLAY_HTML_FILE:-tmp/state/show_status_overlay.html}" 2>/dev/null || true
	chmod 644 "${STATUS_OVERLAY_HTML_FILE:-tmp/state/status_overlay.html}" 2>/dev/null || true
	# eventOverlay 指標も更新 (旧 generate_show_status_overlay と同挙動)
	if [ -f "$ELOOP_LIB_DIR/generate_event_overlay.py" ]; then
		EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR" \
		EVENT_OVERLAY_COMMENT_GEN_STATE="${COMMENT_GEN_STATE_FILE:-tmp/state/.comment_gen_state}" \
		EVENT_OVERLAY_RADIO_STATE="${RADIO_STATE_FILE:-tmp/state/.radio_state}" \
		python3 "$ELOOP_LIB_DIR/generate_event_overlay.py" \
			"$EVENT_OVERLAY_EVENTS_FILE" \
			"$EVENT_OVERLAY_HTML_FILE" \
			"$EVENT_OVERLAY_KEEP_EVENTS" \
			"$EVENT_OVERLAY_VISIBLE_SEC" \
			"$CODEX_WORK_OVERLAY_STATE_FILE" >/dev/null 2>&1 || true
	fi
	printf 'generated:%s\n' "$out_file"
}

render_event_overlay_indicators() { :; }

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
		tmux new-session -d -s "$tmux_session" "cd '$PWD' && exec ./generate_soren_overlay.sh watch '$interval' >> '$log_file' 2>&1"
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
	./obs_browser_source.sh ensure "${OBS_DASHBOARD_SCENE:-soren}" "${SOREN_OVERLAY_SOURCE:-sorenOverlay}" "$out_file" "$width" "$height" "$visibility"
	;;
*)
	echo "usage: $0 once|watch [interval_sec]|start [interval_sec]|stop|ensure-obs [show|hide]" >&2
	exit 2
	;;
esac
