#!/usr/bin/env bash
# issue #132 Phase 1: 目的関数の version が永続レコードに乗ること。
#
# `eval` は建国ボーナス込みの終局評価で、型ボーナス表 (T16=25402) が 2026-09 に
# 追加されました。それ以前の ledger 行・rolling entry・current_run は同じ
# `eval`/`scores` というキー名の、違う目的空間の値です。version が乗っていないと
# 旧 EVAL_SCORE と新目的値を 1 つの標本へ混ぜ、A/B の効果推定が評価関数の差と
# 候補の差を合わせた意味不明な量になります。
#
# 実 bash 関数 (_ab_record_game / update_rolling_scores /
# _update_current_strategy_run) を subprocess で呼んで実際のファイルを検証します。
set -u
cd "$(dirname "$0")/.." || exit 1
fail=0
t() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: expected [$3] got [$2]"; fail=1; fi; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# ── 1. A/B ledger 行に version が乗る ────────────────────────────────────
export TMP_STATE_DIR="$tmp"
log() { :; }
AB_STATE_FILE="$tmp/ab_state.json"; AB_GAMES_FILE="$tmp/ab_games.jsonl"
AB_ABORT_FILE="$tmp/ab_abort"; AB_ALT_FILE="$tmp/alt.py"; STRATEGY_FILE="strategy.py"
printf '{"a_hash":"aaaaaaaaaaaa","b_hash":"bbbbbbbbbbbb","pattern":"ABBA","games_recorded":0}' > "$AB_STATE_FILE"
# shellcheck source=/dev/null
. strategy/ab_interleave.sh

arc="$tmp/archive.jsonl"
python3 - "$arc" <<'PY'
import json, sys
rows = []
for turn in range(1, 6):
    pieces = [{"id": 1, "type": 3, "x": 0.0, "y": -4.0}]
    if turn >= 3:
        pieces.append({"id": 2, "type": 15, "x": 1.0, "y": -3.0})
    rows.append({"turn": turn, "piece_count": len(pieces), "strategy_hash": "bbbbbbbbbbbb",
                 "state_snapshot": {"pieces": pieces}, "deadline_crossed": False,
                 "analyzer_modes": {"wall_clamp": 1, "landing_arc": 0}})
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows))
PY

EXPECT_VER=$(python3 -c "from lib.objective import OBJECTIVE_SCHEMA_VERSION as v; print(v)")
: > "$AB_GAMES_FILE"
AB_ARM="B"; AB_IDX=1; AB_HASH="bbbbbbbbbbbb"
_ab_record_game 1234 5678 5 "$arc" "false" "false" >/dev/null 2>&1
read -r line < "$AB_GAMES_FILE"
t "A/B ledger 行に objective_schema_version が乗る" \
  "$(printf '%s' "$line" | python3 -c "import json,sys;print(json.load(sys.stdin).get('objective_schema_version'))")" \
  "$EXPECT_VER"
t "version は lib/objective の定義と一致" \
  "$(printf '%s' "$line" | python3 -c "import json,sys;v=json.load(sys.stdin).get('objective_schema_version');print(v=='$EXPECT_VER')")" \
  "True"

# ── 2. rolling_scores / current_strategy_run に version が乗る ───────────
cat > "$tmp/run1.sh" <<SH
set -e
source ./core/config.sh
log() { :; }
ROLLING_SCORES_FILE="$tmp/rolling_scores.json"
CURRENT_STRATEGY_RUN_FILE="$tmp/current_strategy_run.json"
DEAD_MONITOR_FILE="$tmp/instadeath_monitor.json"
TMP_STATE_DIR="$tmp/tmp_state"
STRATEGY_HASH_ARCHIVE_DIR="$tmp/by_hash"
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR="$tmp/by_hash_perm"
HASH_ARCHIVE_PRUNE_BACKGROUND=0
STRATEGY_FILE="$tmp/nonexistent_strategy.py"
mkdir -p "$tmp/tmp_state"
source ./strategy/regression.sh
source ./strategy/improve.sh
ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 10000 ''
_update_current_strategy_run h1 10000 ''
SH
bash "$tmp/run1.sh" >/dev/null 2>&1
rc=$?
t "rolling/current-run 更新が成功する" "$rc" "0"
t "rolling_scores entry に version が乗る" \
  "$(python3 -c "import json;print(json.load(open('$tmp/rolling_scores.json'))['h1'].get('objective_schema_version'))")" \
  "$EXPECT_VER"
t "current_strategy_run に version が乗る" \
  "$(python3 -c "import json;print(json.load(open('$tmp/current_strategy_run.json')).get('objective_schema_version'))")" \
  "$EXPECT_VER"
t "rolling の scores は従来どおり" \
  "$(python3 -c "import json;print(json.load(open('$tmp/rolling_scores.json'))['h1']['scores'])")" \
  "[10000]"

# ── 3. version が無い旧 entry は上書きされない (provenance を変えない) ───
python3 - "$tmp" <<'PY'
import json, sys
p = sys.argv[1] + "/rolling_scores.json"
rs = json.load(open(p))
rs["h1"]["objective_schema_version"] = "soviet-terminal-v0-legacy"
json.dump(rs, open(p, "w"))
PY
cat > "$tmp/run2.sh" <<SH
set -e
source ./core/config.sh
log() { :; }
ROLLING_SCORES_FILE="$tmp/rolling_scores.json"
CURRENT_STRATEGY_RUN_FILE="$tmp/current_strategy_run.json"
DEAD_MONITOR_FILE="$tmp/instadeath_monitor.json"
TMP_STATE_DIR="$tmp/tmp_state"
STRATEGY_HASH_ARCHIVE_DIR="$tmp/by_hash"
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR="$tmp/by_hash_perm"
HASH_ARCHIVE_PRUNE_BACKGROUND=0
STRATEGY_FILE="$tmp/nonexistent_strategy.py"
mkdir -p "$tmp/tmp_state"
source ./strategy/regression.sh
source ./strategy/improve.sh
ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 11000 ''
SH
bash "$tmp/run2.sh" >/dev/null 2>&1
t "既存 entry の version は上書きしない" \
  "$(python3 -c "import json;print(json.load(open('$tmp/rolling_scores.json'))['h1'].get('objective_schema_version'))")" \
  "soviet-terminal-v0-legacy"
t "version が違う entry は同じ標本にできない (mixed_versions が検出)" \
  "$(python3 -c "
import sys; sys.path.insert(0, '.')
import json
from lib.objective import mixed_versions
rs = json.load(open('$tmp/rolling_scores.json'))
print(len(mixed_versions([rs['h1'], {'objective_schema_version': 'soviet-terminal-v1'}])))")" \
  "2"

# Existing unversioned history must remain unknown when a new score is appended.
python3 - "$tmp" <<'PYCODE'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
for name in ("rolling_scores.json", "current_strategy_run.json"):
    path = root / name
    data = json.loads(path.read_text())
    entry = data["h1"] if name == "rolling_scores.json" else data
    entry.pop("objective_schema_version", None)
    path.write_text(json.dumps(data))
PYCODE
bash "$tmp/run1.sh" >/dev/null 2>&1
t "version 不明の既存 rolling 履歴を新規 version と偽認しない" \
  "$(python3 -c "import json;print(json.load(open('$tmp/rolling_scores.json'))['h1'].get('objective_schema_version'))")" "None"
t "version 不明の既存 current run を新規 version と偽認しない" \
  "$(python3 -c "import json;print(json.load(open('$tmp/current_strategy_run.json')).get('objective_schema_version'))")" "None"

# ── 4. ab_report が version 混在を黙って隠さない ─────────────────────────
games="$tmp/mixed_games.jsonl"
python3 - "$games" <<'PY'
import json, sys
rows = []
for i in range(8):
    rows.append({"idx": i, "arm": "ABBA"[i % 4], "eval": 1600.0 + i, "score": 900.0 + i,
                 "turns": 90, "tainted": False, "archive": "",
                 # 後半 4 行だけが新しい evaluator。前半は version 無し (= 旧履歴)
                 **({"objective_schema_version": "soviet-terminal-v1"} if i >= 4 else {})})
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows))
PY
out=$(python3 tools/ab_report.py --games "$games" --state "$tmp/ab_state.json" --history "$tmp" 2>&1)
t "version 混在を report が出す" "$(printf '%s' "$out" | grep -c 'objective schema versions MIXED')" "1"
js=$(python3 tools/ab_report.py --games "$games" --state "$tmp/ab_state.json" --history "$tmp" --json 2>&1)
t "JSON にも objective_versions_mixed が出る" \
  "$(printf '%s' "$js" | python3 -c "import json,sys;print(json.load(sys.stdin)['objective_versions_mixed'])")" "True"

# 全部が同じ version なら警告は出ない
games2="$tmp/uniform_games.jsonl"
python3 - "$games2" <<'PY'
import json, sys
rows = []
for i in range(8):
    rows.append({"idx": i, "arm": "ABBA"[i % 4], "eval": 1600.0 + i, "score": 900.0 + i,
                 "turns": 90, "tainted": False, "archive": "",
                 "objective_schema_version": "soviet-terminal-v1"})
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows))
PY
out2=$(python3 tools/ab_report.py --games "$games2" --state "$tmp/ab_state.json" --history "$tmp" 2>&1)
t "version が揃っていれば警告は出ない" "$(printf '%s' "$out2" | grep -c 'MIXED')" "0"

exit $fail
