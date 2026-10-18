#!/bin/bash
# Repair current strategy score state from archived game histories.
#
# This is a surrounding bookkeeping tool. It does not modify strategy logic.

set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=/dev/null
source ./eloop_lib.sh
# Existing library helpers are not all nounset-clean when reused outside the main loop.
set +u

# このスクリプトは record_completed_game_for_adaptive_improvement() と
# soren_loop.sh から子プロセスとして起動されるため、eloop.sh で export された
# LAST_RAW_SCORE/LAST_TURNS をそのまま継承する。過去アーカイブの再生に
#「いま終わった試合」の raw/turns を刻印すると、即死判別器(#3 raw==0比率・
# #4 turns中央値)が汚染される (2026-08-20 Phase 1 レビュー R1)。
unset LAST_RAW_SCORE LAST_TURNS 2>/dev/null || true
export INSTADEATH_MONITOR_UPDATE=0

limit="${1:-12}"
case "$limit" in
	''|*[!0-9]*) limit=12 ;;
esac
[ "$limit" -lt 1 ] && limit=1
[ "$limit" -gt 50 ] && limit=50

current_hash=$(python3 - "$CURRENT_STRATEGY_RUN_FILE" <<'PY' 2>/dev/null || true
import json
import sys

try:
    d = json.load(open(sys.argv[1]))
    print(str(d.get("hash", "") or ""))
except Exception:
    pass
PY
)

if [ -z "$current_hash" ]; then
	current_hash=$(python3 extract_decide_hash.py "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true)
fi
if [ -z "$current_hash" ]; then
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || true)
fi

[ -n "$current_hash" ] || {
	echo "repair_current_run: current hash unavailable" >&2
	exit 1
}

repair_list="${TMP_STATE_DIR:-tmp/state}/current_run_repair_candidates.tsv"
python3 - "$CURRENT_STRATEGY_RUN_FILE" "$HISTORY_DIR" "$limit" "$current_hash" >"$repair_list" <<'PY'
import glob
import json
import os
import sys

# issue #132 Phase 1: 型ボーナス表は lib/objective.py の単一定義。以前はここに
# `tb` の写しを持っていました。eloop.sh (試合中の EVAL_SCORE) と
# wildcard_parallel.py (候補選抜) にも同じ写しがあり、repair だけが古いままだと
# seed される current_strategy_run の scores が試合中の評価と違う目的空間の値に
# なり、同じ hash の標本が混ざります。values は修正後と同一です。
# この script は repo root から起動される (下の relpath/glob が相対パスを使う)
# ので、cwd をそのまま import path に使います。
if "" not in sys.path and "." not in sys.path:
    sys.path.insert(0, ".")
from lib.objective import eval_score as _objective_eval_score

current_file, history_dir, limit_raw, current_hash = sys.argv[1:5]
try:
    limit = max(1, min(50, int(limit_raw)))
except Exception:
    limit = 12

known = set()
first_known = ""
try:
    current = json.load(open(current_file))
    archives = [str(x) for x in current.get("_recent_archives", []) or []]
    known.update(archives)
    if archives:
        first_known = min(archives)
except Exception:
    pass

def eval_score(path):
    last = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if raw:
                    try:
                        last = json.loads(raw)
                    except Exception:
                        pass
    except Exception:
        return None
    if not isinstance(last, dict):
        return None
    try:
        raw_score = int(last.get("score", 0) or 0)
    except Exception:
        raw_score = 0
    types = []
    if isinstance(last.get("final_types"), list):
        for item in last.get("final_types") or []:
            try:
                types.append(int(item))
            except Exception:
                pass
    if not types:
        for piece in ((last.get("state_snapshot") or {}).get("pieces") or []):
            try:
                types.append(int(piece.get("type", 0) or 0))
            except Exception:
                pass
    return _objective_eval_score(raw_score, types, bool(last.get("soviet_created")))

def history_strategy_hash(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                h = str(row.get("strategy_hash", "") or "")
                if h:
                    return h
    except Exception:
        return ""
    return ""

paths = sorted(glob.glob(os.path.join(history_dir, "[0-9]*_score*.jsonl")))[-limit:]
for path in paths:
    rel = os.path.relpath(path, ".")
    if first_known and rel < first_known:
        continue
    if rel in known or path in known:
        continue
    if current_hash and history_strategy_hash(path) != current_hash:
        continue
    score = eval_score(path)
    if score is None:
        continue
    print(f"{score}\t{rel}")
PY
repair_items=()
while IFS= read -r line; do
	repair_items+=("$line")
done <"$repair_list"
rm -f "$repair_list" 2>/dev/null || true

if [ "${#repair_items[@]}" -eq 0 ]; then
	echo "repair_current_run: no missing archives for hash=${current_hash}"
	exit 0
fi

count=0
for item in "${repair_items[@]}"; do
	score="${item%%$'\t'*}"
	archive_file="${item#*$'\t'}"
	if [ -z "$score" ] || [ -z "$archive_file" ] || [ "$score" = "$archive_file" ]; then
		continue
	fi
	update_rolling_scores "$score" "$archive_file"
	_update_current_strategy_run "$current_hash" "$score" "$archive_file"
	count=$((count + 1))
done

echo "repair_current_run: repaired ${count} archive(s) for hash=${current_hash}"
