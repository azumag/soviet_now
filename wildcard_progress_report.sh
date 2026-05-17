#!/bin/bash
# wildcard_progress_report.sh - low-noise audio/overlay milestones for active WILDCARD evaluations.

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a
# shellcheck source=/dev/null
source ./eloop_lib.sh

[ "${WILDCARD_PROGRESS_AUDIO_ENABLED:-1}" = "1" ] || exit 0

python3 - \
	"${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" \
	"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
	"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" \
	"${WILDCARD_PROGRESS_AUDIO_STATE_FILE:-tmp/state/wildcard_progress_audio_last.json}" \
	"${WILDCARD_PROGRESS_AUDIO_MILESTONES:-1,3,6,9,12}" \
	"${WILDCARD_PROGRESS_AUDIO_MIN_DELTA:-0}" <<'PY' >"${TMP_STATE_DIR:-tmp/state}/wildcard_progress_report.env.tmp"
import json
import math
import os
import shlex
import sys
import time

origin_file, run_file, anchor_file, state_file, milestones_raw, min_delta_raw = sys.argv[1:7]

def load(path):
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
        return 0.0
    mean = sum(vals) / len(vals)
    if len(vals) > 1:
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        lcb = mean - 1.28 * (math.sqrt(var) / math.sqrt(len(vals)))
    else:
        lcb = mean
    return 0.55 * quantile(vals, 0.50) + 0.30 * quantile(vals, 0.25) + 0.15 * lcb

origins = load(origin_file)
run = load(run_file)
anchor = load(anchor_file)
current_hash = str(run.get("hash", "") or "")
if not current_hash or current_hash not in origins:
    raise SystemExit

scores = run.get("scores", []) or []
n = int(run.get("games_total", 0) or len(scores))
if n <= 0:
    raise SystemExit

try:
    milestones = sorted({int(x.strip()) for x in milestones_raw.split(",") if x.strip()})
except Exception:
    milestones = [1, 3, 6, 9, 12]
eligible = [m for m in milestones if n >= m]
if not eligible:
    raise SystemExit
milestone = eligible[-1]

comp = composite(scores)
try:
    anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
except Exception:
    anchor_comp = 0.0
delta = int(round(comp - anchor_comp)) if anchor_comp else 0
try:
    min_delta = int(float(min_delta_raw or 0))
except Exception:
    min_delta = 0

kind = "milestone"
if delta >= min_delta and anchor_comp:
    kind = "lead"
key = f"{current_hash}:{milestone}:{kind}"

state = load(state_file)
if state.get("key") == key:
    raise SystemExit

os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
tmp = state_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({
        "key": key,
        "hash": current_hash,
        "milestone": milestone,
        "kind": kind,
        "n": n,
        "comp": int(round(comp)),
        "anchor_comp": int(round(anchor_comp)) if anchor_comp else 0,
        "delta": delta,
        "ts": int(time.time()),
    }, f, ensure_ascii=False)
os.replace(tmp, state_file)

short = current_hash[:4]
message = (
    f"WILDCARD {short} は {n}/12 試合まで評価済み。"
    f" composite {int(round(comp))}、anchor 比 {delta:+d}。"
    " 現在は脱出候補として追跡継続します。"
)
print("message=" + shlex.quote(message))
print("title=" + shlex.quote(f"WILDCARD {short} {n}/12 {delta:+d}"))
print("detail=" + shlex.quote(f"hash={current_hash} n={n} milestone={milestone} comp={int(round(comp))} delta={delta:+d} kind={kind}"))
PY

env_file="${TMP_STATE_DIR:-tmp/state}/wildcard_progress_report.env.tmp"
[ -s "$env_file" ] || exit 0
# shellcheck source=/dev/null
. "$env_file"
rm -f "$env_file" 2>/dev/null || true

if [ -n "${message:-}" ]; then
	enqueue_audio_text "$message" "wildcard_progress" "${SYSTEM_PROGRESS_AUDIO_SPEAKER:-${SOREN91_VOICEVOX_SPEAKER:-46}}" || true
fi
if [ -x ./overlay_notify.sh ] && [ -n "${title:-}" ]; then
	./overlay_notify.sh worker "$title" "${detail:-}" "info" >/dev/null 2>&1 || true
fi

printf '%s\n' "${message:-}"
