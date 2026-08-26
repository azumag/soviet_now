#!/usr/bin/env python3
"""Categorized CPU usage sampler: sums per-process CPU ticks (incl. short-lived procs seen
at 1s granularity) over N seconds and buckets them by role. Prints % of one core."""
import os, re, sys, time
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
CLK = os.sysconf('SC_CLK_TCK')
RULES = [
  ('render:chrome-gpu(main)', r'soviet_local_chromium_profile.*--type=gpu-process|--type=gpu-process.*soviet_local_chromium_profile'),
  ('render:chrome-renderer(main)', r'soviet_local_chromium_profile.*--type=renderer|--type=renderer.*soviet_local_chromium_profile'),
  ('render:chrome-other(main)', r'soviet_local_chromium_profile'),
  ('render:iso-probe-chrome', r'iso_probe_profile'),
  ('render:Xvfb/xfwm4', r'^(/usr/bin/)?(Xvfb|xfwm4)\b'),
  ('stream:ffmpeg-x11grab', r'ffmpeg .*x11grab'),
  ('stream:pulse/stunnel/nginx', r'pulseaudio|stunnel|nginx'),
  ('voice:voicevox-engine', r'/opt/voicevox/'),
  ('voice:playback(paplay/ffplay/sox)', r'\b(paplay|ffplay|sox|aplay|play)\b'),
  ('voice:audio_worker/say/cc', r'audio_worker\.sh|say_enqueue|closed_captions\.py|voicevox_|say_|audio_'),
  ('ai:llm-cli(codex/opencode/claude)', r'\b(codex|opencode|claude|node .*opencode|node .*codex)\b'),
  ('ai:chat/radio/kick/youtube workers', r'chat_worker|radio_worker|kick_worker|youtube_worker|comment\.sh|radio_|jiji|kick_chat_daemon'),
  ('ai:improve/strategy-runner(local AI)', r'improve_daemon|strategy_runner\.py|eloop|soren_loop\.sh|extract_decide_hash|strategy\.py'),
  ('bridge:node soviet_local', r'node soviet_local\.mjs'),
  ('ops:dashboard/status/monitor', r'status_dashboard|show_status|generate_dashboard|monitor_|watchdog|codex_bug_dispatcher|minimax_success_obs|webui|direct_stream\.py'),
  ('shell:bash/python(short-lived misc)', r'^(/bin/|/usr/bin/)?(bash|sh|python3|python|grep|sed|awk|date|cat|jq|curl|timeout|sleep|tmux)\b'),
]
def cmd(pid):
    try:
        with open(f'/proc/{pid}/cmdline','rb') as f: c = f.read().replace(b'\0', b' ').decode('utf8','replace').strip()
        if not c:
            with open(f'/proc/{pid}/comm') as f: c = '[' + f.read().strip() + ']'
        return c
    except Exception: return None
def ticks(pid):
    try:
        with open(f'/proc/{pid}/stat') as f: s = f.read()
        p = s[s.rindex(')')+2:].split()
        return int(p[11]) + int(p[12])
    except Exception: return None
def sys_ticks():
    with open('/proc/stat') as f: p = f.readline().split()
    v = list(map(int, p[1:]))
    return sum(v) - v[3] - v[4]  # busy = total - idle - iowait
cat = {}; names = {}
def classify(pid):
    if pid in names: return names[pid]
    c = cmd(pid)
    if c is None: return None
    for name, rx in RULES:
        if re.search(rx, c): names[pid] = name; return name
    names[pid] = 'other:' + c[:40]; return names[pid]
prev = {}; totals = {}; sys0 = sys_ticks(); t0 = time.time()
for pid in os.listdir('/proc'):
    if pid.isdigit(): t = ticks(pid); prev[int(pid)] = t if t is not None else 0
for _ in range(N):
    time.sleep(1)
    cur = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        pid = int(pid); t = ticks(pid)
        if t is None: continue
        cur[pid] = t
        d = t - prev.get(pid, 0)
        if d > 0:
            k = classify(pid)
            if k: totals[k] = totals.get(k, 0) + d
    prev = cur
el = time.time() - t0; sysd = sys_ticks() - sys0
cap = sum(totals.values())
print(f"window={el:.0f}s  system busy={sysd/CLK/el*100:.0f}% of one core (4 cores = 400%)  captured={cap/CLK/el*100:.0f}%  uncaptured(short-lived<1s)={(sysd-cap)/CLK/el*100:.0f}%")
agg = {}
for k, v in sorted(totals.items(), key=lambda x: -x[1]):
    pct = v/CLK/el*100
    if pct < 0.5: continue
    print(f"  {pct:6.1f}%  {k}")
for k, v in totals.items():
    g = k.split(':')[0]; agg[g] = agg.get(g, 0) + v
print("by group:", "  ".join(f"{g}={v/CLK/el*100:.0f}%" for g, v in sorted(agg.items(), key=lambda x: -x[1])))
