#!/bin/bash
# monitor_report_stale_report.sh - low-noise audio/overlay notice for stale Claude monitor reports.

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a
# shellcheck source=/dev/null
source ./eloop_lib.sh

[ "${MONITOR_REPORT_AUDIO_ENABLED:-1}" = "1" ] || exit 0

python3 - \
	"${SOREN_MONITOR_REPORT_FILE:-/tmp/soren_report.md}" \
	"${MONITOR_REPORT_AUDIO_STATE_FILE:-tmp/state/monitor_report_audio_last.json}" \
	"${MONITOR_REPORT_STALE_SEC:-900}" \
	"${MONITOR_REPORT_OLD_SEC:-3600}" \
	"${MONITOR_REPORT_AUDIO_MIN_INTERVAL_SEC:-900}" \
	"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
	"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" \
	"${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" \
	"${MONITOR_REPORT_STATUS_FILE:-tmp/state/monitor_report_status.json}" <<'PY' >"${TMP_STATE_DIR:-tmp/state}/monitor_report_stale_report.env.tmp"
import datetime as dt
import json
import math
import os
import re
import shlex
import sys
import time

report_file, state_file, stale_raw, old_raw, interval_raw, current_run_file, anchor_file, origin_file, status_file = sys.argv[1:10]

def as_int(value, default):
    try:
        return int(float(value))
    except Exception:
        return default

def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def quantile(vals, p):
    xs = sorted(float(v) for v in vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def composite(scores):
    vals = [float(v) for v in scores if isinstance(v, (int, float))]
    if not vals:
        return 0
    mean = sum(vals) / len(vals)
    if len(vals) > 1:
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        lcb = mean - 1.28 * (math.sqrt(var) / math.sqrt(len(vals)))
    else:
        lcb = mean
    return int(round(0.55 * quantile(vals, 0.50) + 0.30 * quantile(vals, 0.25) + 0.15 * lcb))

def as_optional_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default

def parse_report_summary(text):
    stats = {
        "eval_last50": None,
        "regression_streak": None,
        "last_event": None,
        "anchor_comp": None,
        "current_comp": None,
        "anchor_hash": None,
    }
    # レポートは `**regression_streak=47**` / `regression_streak=47` / `**regression_streak=**` の混在
    regression_match = re.search(r"\*{0,2}\s*regression_streak\s*=?\s*\*{0,2}\s*([0-9]+)\s*\*{0,2}", text, re.I)
    if regression_match:
        stats["regression_streak"] = as_optional_int(regression_match.group(1), None)
    # last_event は `last_event=REGRESSION` / `last_event=**REGRESSION**` を許容
    last_event_match = re.search(r"\*{0,2}\s*last_event\s*=?\s*([A-Za-z0-9_]+)\s*\*{0,2}", text, re.I)
    if last_event_match:
        stats["last_event"] = last_event_match.group(1)
    # eval_last50 は `eval last50=...` / `eval_last50` / マークダウン強調ありを許容
    eval_last50_match = re.search(r"\*{0,2}\s*eval(?:\s+|_)?last50\s*=?\s*([0-9]+)\s*\*{0,2}", text, re.I)
    if eval_last50_match:
        stats["eval_last50"] = as_optional_int(eval_last50_match.group(1), None)
    # anchor は `anchor `8cde...` comp=11007` / `anchor hash=... comp 11007` を許容
    anchor_match = re.search(
        r"\*{0,2}\s*anchor\s+(?:`?([0-9a-f]{8,40})`?|.*hash=\s*`?([0-9a-f]{8,40})`?)[^\n]*?(?:comp|score)\s*=?\s*([0-9]+)",
        text,
        re.I,
    )
    if anchor_match:
        stats["anchor_hash"] = anchor_match.group(1) or anchor_match.group(2)
        stats["anchor_comp"] = as_optional_int(anchor_match.group(3), None)
    # curr_comp は `curr_comp 11310 vs anchor` / `curr_comp=11310` を許容
    current_match = re.search(r"\*{0,2}\s*curr_comp\s*=?\s*([0-9]+)\s*(?:vs\s*anchor|$|\*{0,2})", text, re.I)
    if current_match:
        stats["current_comp"] = as_optional_int(current_match.group(1), None)
    return stats

def live_summary():
    run = load_json(current_run_file)
    if not run:
        return "", ""
    h = str(run.get("hash", "") or "")
    n = int(run.get("games_total", 0) or len(run.get("scores", []) or []))
    comp = composite(run.get("scores", []) or [])
    anchor = load_json(anchor_file)
    try:
        anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
    except Exception:
        anchor_comp = 0.0
    delta = int(round(comp - anchor_comp)) if anchor_comp and comp else 0
    origins = load_json(origin_file)
    origin = origins.get(h) if h and isinstance(origins.get(h), dict) else {}
    origin_type = str(origin.get("origin_type", "") or "")
    label = {
        "archive_restart": "Arc",
        "escape_ai": "AI",
        "wildcard": "Wild",
    }.get(origin_type, "Run")
    source = ""
    if origin_type == "archive_restart":
        source_bits = []
        try:
            source_russia = int(origin.get("source_russia_count", 0) or 0)
            if source_russia:
                source_bits.append(f"R{source_russia}")
        except Exception:
            pass
        try:
            source_best_type = int(origin.get("source_best_max_type", 0) or 0)
            if source_best_type:
                source_bits.append(f"T{source_best_type}")
        except Exception:
            pass
        if source_bits:
            source = " " + "".join(source_bits)
    live = f"{label} {h[:4]} {n}/12 c{comp} d{delta:+d}{source}".strip()
    detail = f"live_hash={h} live_origin_type={origin_type or 'normal'} live_n={n} live_comp={comp} live_delta={delta:+d}"
    return live, detail

def deadline_crossing_audit(path="game_history/latest.jsonl"):
    total = 0
    no_merge = 0
    no_merge_with_safe = 0
    last_no_merge = {}
    last_no_merge_with_safe = {}
    paths = []
    run = load_json(current_run_file)
    for raw in run.get("_recent_archives", []) or []:
        if isinstance(raw, str) and raw:
            paths.append(raw)
    paths.append(path)
    seen_paths = set()
    for audit_path in paths:
        if audit_path in seen_paths or not os.path.exists(audit_path):
            continue
        seen_paths.add(audit_path)
        try:
            f = open(audit_path, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not row.get("decision_crosses_deadline"):
                    continue
                total += 1
                if str(row.get("best_merge_grade") or "NO") == "NO":
                    safe_candidate_count = int(row.get("deadline_safe_candidate_count") or 0)
                    no_merge += 1
                    last_no_merge = {
                        "file": audit_path,
                        "turn": row.get("turn"),
                        "score": row.get("score"),
                        "safe_candidate_count": safe_candidate_count,
                        "candidate_count": int(row.get("deadline_candidate_count") or 0),
                        "reason": str(row.get("decision_reason") or "")[:160],
                    }
                    if safe_candidate_count > 0:
                        no_merge_with_safe += 1
                        last_no_merge_with_safe = dict(last_no_merge)
    return {
        "deadline_crossing_count": total,
        "deadline_no_merge_count": no_merge,
        "deadline_no_merge_with_safe_count": no_merge_with_safe,
        "deadline_no_merge_last": last_no_merge,
        "deadline_no_merge_with_safe_last": last_no_merge_with_safe,
    }

stale_sec = max(1, as_int(stale_raw, 900))
old_sec = max(stale_sec, as_int(old_raw, 3600))
min_interval = max(0, as_int(interval_raw, 1800))
now = int(time.time())

text = ""
title = "soren monitor"
mtime = 0
if os.path.exists(report_file):
    try:
        with open(report_file, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        mtime = int(os.path.getmtime(report_file))
        for raw in text.splitlines():
            cleaned = raw.strip().lstrip("# ").strip()
            if cleaned:
                title = cleaned[:32]
                break
    except Exception:
        title = "soren monitor unreadable"
else:
    status = "missing"
    age = 0
    key = "missing"
    message = "メリケンAI監視レポートが見つかりません。外部監視が落ちている可能性があります。"
    print("message=" + shlex.quote(message))
    print("title=" + shlex.quote("Monitor missing"))
    print("detail=" + shlex.quote(f"file={report_file}"))
    raise SystemExit

report_epoch = 0
m = re.search(r"最終更新:\s*(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+JST", text)
if m:
    try:
        stamp = dt.datetime.strptime(
            f"{m.group(1)} {m.group(2)}:{m.group(3)} +0900",
            "%Y-%m-%d %H:%M %z",
        )
        report_epoch = int(stamp.timestamp())
    except Exception:
        report_epoch = 0

age = max(0, now - (report_epoch or mtime or now))
status = "fresh" if age < stale_sec else ("old" if age >= old_sec else "stale")
key = f"{status}:{report_epoch or mtime}"

try:
    state = json.load(open(state_file, encoding="utf-8"))
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}

last_ts = as_int(state.get("ts", 0), 0)
should_notify = status != "fresh"
if should_notify and state.get("key") == key and now - last_ts < min_interval:
    should_notify = False
    message = ""

os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
tmp = state_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({
        "key": key,
        "status": status,
        "age": age,
        "report_epoch": report_epoch,
        "mtime": mtime,
        "ts": now,
    }, f, ensure_ascii=False)
os.replace(tmp, state_file)

age_label = fmt_age(age)
live, live_detail = live_summary()
deadline_audit = deadline_crossing_audit()
deadline_no_merge_count = int(deadline_audit.get("deadline_no_merge_count") or 0)
deadline_no_merge_with_safe_count = int(deadline_audit.get("deadline_no_merge_with_safe_count") or 0)
if deadline_no_merge_count:
    suffix = f" deadline_no_merge={deadline_no_merge_count}"
    if deadline_no_merge_with_safe_count:
        suffix += f" safe_available={deadline_no_merge_with_safe_count}"
    live = f"{live} {suffix}".strip() if live else suffix.strip()
    live_detail = f"{live_detail} {suffix}".strip() if live_detail else suffix.strip()
live_text = f" ライブは {live}。" if live else ""
if should_notify:
    message = f"メリケンAI監視レポートが{age_label}更新されていません。{live_text}現在の評価はライブ状態を正として継続監視します。"
    print("message=" + shlex.quote(message))
    print("title=" + shlex.quote(f"Monitor {status} {age_label}"))
    detail = f"{title} file={report_file} age={age_label}"
    if live_detail:
        detail += " " + live_detail
    print("detail=" + shlex.quote(detail))

summary = parse_report_summary(text)
summary_payload = {
	"report_file": report_file,
	"status": status,
	"status_key": key,
	"age_sec": age,
	"age_label": age_label,
	"title": title,
	"report_epoch": report_epoch,
	"mtime": mtime,
	"live_detail": live_detail,
	"live": live,
	"eval_last50": summary.get("eval_last50"),
	"regression_streak": summary.get("regression_streak"),
	"last_event": summary.get("last_event"),
	"anchor_hash": summary.get("anchor_hash"),
	"anchor_comp": summary.get("anchor_comp"),
	"current_comp": summary.get("current_comp"),
    "deadline_crossing_count": deadline_audit.get("deadline_crossing_count"),
    "deadline_no_merge_count": deadline_audit.get("deadline_no_merge_count"),
    "deadline_no_merge_with_safe_count": deadline_audit.get("deadline_no_merge_with_safe_count"),
    "deadline_no_merge_last": deadline_audit.get("deadline_no_merge_last"),
    "deadline_no_merge_with_safe_last": deadline_audit.get("deadline_no_merge_with_safe_last"),
}
os.makedirs(os.path.dirname(status_file) or ".", exist_ok=True)
tmp_status_path = status_file + ".tmp"
with open(tmp_status_path, "w", encoding="utf-8") as f:
    json.dump(summary_payload, f, ensure_ascii=False)
os.replace(tmp_status_path, status_file)
PY

env_file="${TMP_STATE_DIR:-tmp/state}/monitor_report_stale_report.env.tmp"
[ -s "$env_file" ] || exit 0
# shellcheck source=/dev/null
. "$env_file"
rm -f "$env_file" 2>/dev/null || true

if [ -n "${message:-}" ]; then
	enqueue_audio_text "$message" "monitor_report" "${SYSTEM_PROGRESS_AUDIO_SPEAKER:-${SOREN91_VOICEVOX_SPEAKER:-46}}" || true
fi
if [ -x ./overlay_notify.sh ] && [ -n "${title:-}" ]; then
	./overlay_notify.sh system "$title" "${detail:-}" "warn" >/dev/null 2>&1 || true
fi

printf '%s\n' "${message:-}"
