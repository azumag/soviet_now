#!/usr/bin/env python3
"""Generate a lightweight OBS toast overlay from overlay_events.jsonl."""

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def read_events(path: Path, keep: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return events
    for line in lines[-keep:]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events[-keep:]


def main() -> None:
    events_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    keep = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    visible_sec = int(sys.argv[4]) if len(sys.argv) > 4 else 18
    events = read_events(events_path, keep)
    now = int(time.time())
    recent = [e for e in events if now - int(e.get("ts", 0) or 0) <= max(visible_sec * 4, 60)]

    payload = json.dumps(recent[-18:], ensure_ascii=False, separators=(",", ":"))
    doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<style>
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: "Segoe UI", "Helvetica Neue", sans-serif;
}}
#toasts {{
  position: fixed;
  right: 24px;
  bottom: 28px;
  width: 560px;
  display: flex;
  flex-direction: column-reverse;
  gap: 10px;
}}
.toast {{
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr);
  min-height: 72px;
  color: #eef5ff;
  background: rgba(7, 12, 22, 0.88);
  border: 1px solid rgba(184, 205, 235, 0.22);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.38);
  border-radius: 8px;
  overflow: hidden;
}}
.toast.fresh {{
  animation: slideIn 220ms ease-out;
}}
.bar {{ background: #8ab4ff; }}
.toast.game .bar {{ background: #facc15; }}
.toast.worker .bar {{ background: #38bdf8; }}
.toast.chat .bar {{ background: #a78bfa; }}
.toast.radio .bar {{ background: #22c55e; }}
.toast.prediction .bar {{ background: #fb7185; }}
.toast.rollback .bar {{ background: #f97316; }}
.toast.system .bar {{ background: #e5e7eb; }}
.content {{ padding: 10px 13px 11px; min-width: 0; }}
.head {{
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}}
.title {{
  font-size: 20px;
  font-weight: 800;
  line-height: 1.15;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.time {{
  margin-left: auto;
  color: rgba(238, 245, 255, 0.62);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}}
.body {{
  margin-top: 5px;
  color: #d8e4f2;
  font-size: 16px;
  line-height: 1.28;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}}
.empty {{ display: none; }}
@keyframes slideIn {{
  from {{ transform: translateX(18px); opacity: 0; }}
  to {{ transform: translateX(0); opacity: 1; }}
}}
</style>
</head>
<body>
<div id="toasts"></div>
<script>
const EVENTS = {payload};
const VISIBLE_SEC = {visible_sec};
const ANIMATE_MAX_AGE = 3;
const now = Math.floor(Date.now() / 1000);
const container = document.getElementById('toasts');
function pad(n) {{ return String(n).padStart(2, '0'); }}
function timeLabel(ts) {{
  const d = new Date(ts * 1000);
  return `${{pad(d.getHours())}}:${{pad(d.getMinutes())}}:${{pad(d.getSeconds())}}`;
}}
for (const ev of EVENTS.slice().reverse()) {{
  const age = now - Number(ev.ts || 0);
  if (age > VISIBLE_SEC) continue;
  const item = document.createElement('section');
  const fresh = age <= ANIMATE_MAX_AGE ? ' fresh' : '';
  item.className = `toast ${{ev.category || 'worker'}}${{fresh}}`;
  item.innerHTML = `<div class="bar"></div><div class="content"><div class="head"><div class="title"></div><div class="time"></div></div><div class="body"></div></div>`;
  item.querySelector('.title').textContent = ev.title || ev.category || 'event';
  item.querySelector('.time').textContent = timeLabel(Number(ev.ts || now));
  item.querySelector('.body').textContent = ev.body || '';
  container.appendChild(item);
}}
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".event_overlay.", suffix=".html", dir=str(out_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, out_path)
    try:
        out_path.chmod(0o644)
    except OSError:
        pass


if __name__ == "__main__":
    main()
