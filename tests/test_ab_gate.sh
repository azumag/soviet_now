#!/usr/bin/env bash
# strategy/ab_gate.sh の単体テスト: 候補出力 → 境界での A/B 開始 (dry-run / 実行) → 逐次判定 → finish A/B。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_gate_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tools" "$WORK/lib" "$WORK/strategy_helpers" "$WORK/strategy_versions/by_hash" "$WORK/strategy_versions_archive/by_hash"
cp "$ROOT/extract_decide_hash.py" "$ROOT/set_toggle.sh" "$WORK/"
cp "$ROOT/tools/ab_report.py" "$ROOT/tools/ab_decide.py" "$WORK/tools/"
cp "$ROOT/lib/eval_stats.py" "$WORK/lib/" 2>/dev/null || true
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cp "$ROOT/strategy_helpers/"*.py "$WORK/strategy_helpers/" 2>/dev/null || true
python3 - "$ROOT/strategy.py" "$WORK/alt.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read(); old="best_x = 0.0905"; assert s.count(old)==1
open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0906"))
PY
cd "$WORK"
echo 'REGRESSION_DISABLED=0' > .env
echo 'AB_GATE_ENABLED=1' >> .env
echo 'AB_GATE_DRY_RUN=1' >> .env
echo 7 > game_count.txt
A=$(python3 extract_decide_hash.py strategy.py); B=$(python3 extract_decide_hash.py alt.py)
LOGS=""
log() { LOGS="$LOGS
$*"; }
TMP_STATE_DIR=tmp/state; STRATEGY_FILE=strategy.py; IMPROVE_LOCK_FILE=tmp/improve.lock; GAME_COUNT_FILE=game_count.txt
REJECTED_HASHES_FILE=tmp/state/rejected_hashes.txt
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }
lastlog() { printf '%s' "$LOGS" | tail -n 1; }

# 1) emit: harvest dir → candidate dir + meta (root 不変)
mkdir -p harvest/logs harvest/strategy_helpers; cp alt.py harvest/strategy.py.staging; echo "changed X" > harvest/logs/change_log.txt; cp strategy_helpers/board_stats.py harvest/strategy_helpers/ 2>/dev/null || true
_ab_gate_emit_candidate harvest "$A" 12 "100 200" && ok || ng "emit"
_ab_gate_candidate_pending && ok || ng "pending after emit"
[ "$(_ab_meta_get cand_hash)" = "$B" ] && [ "$(_ab_meta_get base_hash)" = "$A" ] && ok || ng "meta hashes"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$A" ] && ok || ng "root untouched by emit"
cp strategy.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null 2>&1 && ng "same-hash candidate must not emit" || ok
# candidate_ready_since
_ab_gate_candidate_ready_since 0 "$A" && ok || ng "ready_since base ok"
_ab_gate_candidate_ready_since 0 "deadbeef" && ng "ready_since wrong base" || ok
_ab_gate_candidate_ready_since $(( $(date +%s) + 100 )) "$A" && ng "ready_since future start" || ok

# 2) before_game (dry-run): would start once, no state
_ab_gate_before_game; lastlog | grep -q "would start" && ok || ng "dry-run would start ($(lastlog))"
[ ! -f tmp/state/ab_state.json ] && ok || ng "dry-run must not create state"
n1=$(printf '%s' "$LOGS" | grep -c "would start"); _ab_gate_before_game; n2=$(printf '%s' "$LOGS" | grep -c "would start"); [ "$n1" = "$n2" ] && ok || ng "dry-run logs once ($n1 -> $n2)"

# 3) stale base → discarded
python3 - <<'PY'
import json; p="tmp/state/ab_candidate/meta.json"; d=json.load(open(p)); d["base_hash"]="000000000000"; json.dump(d,open(p,"w"))
PY
_ab_gate_before_game; [ ! -d tmp/state/ab_candidate ] && ok || ng "stale base discarded"

# 4) real start via before_game (dry-run off)
sed -i.bak 's/^AB_GATE_DRY_RUN=.*/AB_GATE_DRY_RUN=0/' .env
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" 12 "100 200" >/dev/null
_ab_gate_before_game
[ -f tmp/state/ab_state.json ] && ok || ng "start created state ($(lastlog))"
grep -q "^REGRESSION_DISABLED=1" .env && grep -q "^SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py" .env && ok || ng "toggles set: $(grep -E '^(REGRESSION|SOREN_AB)' .env | tr '\n' ' ')"
[ "$(_ab_state_get b_hash)" = "$B" ] && [ "$(_ab_state_get a_hash)" = "$A" ] && ok || ng "state hashes"
[ "$(_ab_state_get regression_disabled_before)" = "0" ] && ok || ng "regression_disabled_before recorded"
[ "$(python3 extract_decide_hash.py tmp/revert_strategy.py)" = "$A" ] && ok || ng "revert point = A"
[ ! -d tmp/state/ab_candidate ] && ok || ng "candidate consumed"
export SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py
_ab_active >/dev/null 2>&1 && ok || ng "active after start: $(_ab_active 2>&1)"

# 5) after_game: synthetic games with strong harm → finish A (rejected, files moved, toggles restored)
python3 - "$A" "$B" <<'PY'
import json,sys,random
a,b=sys.argv[1:3]; rng=random.Random(1); rows=[]; idx=0
for k in range(8):
    base=rng.gauss(1600,150)
    for ch in "ABBA":
        v=base+rng.gauss(0,80)-(600 if ch=="B" else 0)
        rows.append({"idx":idx,"arm":ch,"hash":b if ch=="B" else a,"score":v,"eval":v+5000,"turns":90,"tainted":False}); idx+=1
open("tmp/state/ab_games.jsonl","w").write("".join(json.dumps(r)+"\n" for r in rows))
st=json.load(open("tmp/state/ab_state.json")); st["games_recorded"]=len(rows); json.dump(st,open("tmp/state/ab_state.json","w"))
PY
_ab_gate_after_game
printf '%s' "$LOGS" | grep -q "verdict=REJECT_HARM" && ok || ng "verdict logged ($(printf '%s' "$LOGS" | grep AB-GATE | tail -2))"
[ ! -f tmp/state/ab_state.json ] && ok || ng "finish A removed state"
grep -qx "$B" tmp/state/rejected_hashes.txt && ok || ng "B rejected recorded"
grep -q "^REGRESSION_DISABLED=0" .env && grep -q "^SOREN_AB_ALT_STRATEGY=$" .env && ok || ng "toggles restored: $(grep -E '^(REGRESSION|SOREN_AB_ALT)' .env | tr '\n' ' ')"
ls tmp/history/ab_*_games.jsonl >/dev/null 2>&1 && ls tmp/history/ab_*_state.json >/dev/null 2>&1 && ok || ng "history files"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$A" ] && ok || ng "root still A after reject"
# rejected candidate is discarded at the boundary
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null; _ab_gate_before_game; [ ! -d tmp/state/ab_candidate ] && [ ! -f tmp/state/ab_state.json ] && ok || ng "rejected hash discarded"

# 6) finish B adopts: root becomes B, revert = A
rm -f tmp/state/rejected_hashes.txt
python3 - "$ROOT/strategy.py" "$WORK/alt2.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read(); old="best_x = 0.0905"; open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0907"))
PY
C=$(python3 extract_decide_hash.py alt2.py)
cp alt2.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null; _ab_gate_before_game
[ -f tmp/state/ab_state.json ] && ok || ng "second start"
_ab_finish B "test adopt" >/dev/null 2>&1 && ok || ng "finish B rc"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$C" ] && ok || ng "root adopted C"
[ "$(python3 extract_decide_hash.py tmp/revert_strategy.py)" = "$A" ] && ok || ng "revert = previous root"
[ ! -f tmp/state/ab_state.json ] && grep -q "^SOREN_AB_ALT_STRATEGY=$" .env && ok || ng "state cleared after adopt"
python3 -c "import json;rows=[json.loads(l) for l in open('tmp/history/ab_history.jsonl')];assert rows[-1]['winner']=='B' and rows[-2]['winner']=='A',rows" && ok || ng "ab_history entries"

# 7) gate off → before/after are no-ops
sed -i.bak 's/^AB_GATE_ENABLED=.*/AB_GATE_ENABLED=0/' .env
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$C" >/dev/null; _ab_gate_before_game; [ -d tmp/state/ab_candidate ] && [ ! -f tmp/state/ab_state.json ] && ok || ng "gate off no-op"

echo "test_ab_gate: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
