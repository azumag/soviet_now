#!/usr/bin/env bash
# strategy/ab_interleave.sh の単体テスト: 腕の巡回、有効条件 (fail-closed)、記録と tainted 判定、abort。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state"
cp "$ROOT/extract_decide_hash.py" "$WORK/"
cp "$ROOT/strategy.py" "$WORK/strategy.py"
# B 腕: decide() の定数を変えて別 hash にする
python3 - "$ROOT/strategy.py" "$WORK/alt.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read()
old="best_x = 0.0905"
assert s.count(old)==1, s.count(old)
open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0906"))
PY
cd "$WORK"
A=$(python3 extract_decide_hash.py strategy.py); B=$(python3 extract_decide_hash.py alt.py)
[ "$A" != "$B" ] || { echo "FAIL: alt hash equals root"; exit 1; }
log() { echo "[log] $*"; }
GAME_NUM=7
TMP_STATE_DIR=tmp/state
STRATEGY_FILE=strategy.py
IMPROVE_LOCK_FILE=tmp/improve.lock
source "$ROOT/strategy/ab_interleave.sh"
pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }
state() { printf '{"a_hash":"%s","b_hash":"%s","pattern":"%s","games_recorded":%s}\n' "$1" "$2" "$3" "$4" > tmp/state/ab_state.json; }

# 1) 未設定なら不活性 (ログも出ない)
unset SOREN_AB_ALT_STRATEGY
out=$(_ab_active 2>&1); rc=$?
[ $rc -ne 0 ] && [ -z "$out" ] && ok || ng "inactive when unset (rc=$rc out=$out)"

# 2) 条件を順に満たしていく (各段階で不活性の理由が出る)
export SOREN_AB_ALT_STRATEGY=alt.py
echo 'REGRESSION_DISABLED=0' > .env
cp alt.py tmp/state/ab_alt_strategy.py
state "$A" "$B" ABBA 0
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "REGRESSION_DISABLED" && ok || ng "needs REGRESSION_DISABLED=1 ($out)"
echo 'REGRESSION_DISABLED="1"' > .env
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "not paused" && ok || ng "needs improve paused ($out)"
touch tmp/state/improve_daemon.paused
touch tmp/improve.lock
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "lock" && ok || ng "needs no improve lock ($out)"
rm -f tmp/improve.lock
_ab_active >/dev/null 2>&1 && ok || ng "should be active now: $(_ab_active 2>&1)"

# 3) hash 不一致は不活性 (alt が書き換わった / root が別物)
cp strategy.py tmp/state/ab_alt_strategy.py
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "alt hash" && ok || ng "alt hash drift ($out)"
cp alt.py tmp/state/ab_alt_strategy.py
state "deadbeef0000" "$B" ABBA 0
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "root hash" && ok || ng "root hash mismatch ($out)"
state "$A" "$B" ABBA 0

# 4) 腕の巡回 (ABBA / ABAB) と counter
seq=""; for n in 0 1 2 3 4 5 6 7; do state "$A" "$B" ABBA "$n"; _ab_select_arm >/dev/null; seq="$seq$AB_ARM"; done
[ "$seq" = "ABBAABBA" ] && ok || ng "ABBA sequence got $seq"
export SOREN_AB_PATTERN=ABAB
seq=""; for n in 0 1 2 3; do state "$A" "$B" ABAB "$n"; _ab_select_arm >/dev/null; seq="$seq$AB_ARM"; done
[ "$seq" = "ABAB" ] && ok || ng "ABAB sequence got $seq"
export SOREN_AB_PATTERN="xyz"
state "$A" "$B" AB 1; _ab_select_arm >/dev/null; [ "$AB_ARM" = "B" ] && ok || ng "garbage pattern falls back to AB (got $AB_ARM)"
export SOREN_AB_PATTERN=ABBA
state "$A" "$B" ABBA 1; _ab_select_arm >/dev/null
[ "$AB_ARM" = "B" ] && [ "$AB_SOURCE" = "tmp/state/ab_alt_strategy.py" ] && [ "$AB_HASH" = "$B" ] && ok || ng "B arm source/hash ($AB_ARM $AB_SOURCE $AB_HASH)"
state "$A" "$B" ABBA 0; _ab_select_arm >/dev/null
[ "$AB_ARM" = "A" ] && [ "$AB_SOURCE" = "strategy.py" ] && [ "$AB_HASH" = "$A" ] && ok || ng "A arm source/hash"

# 5) 記録: snapshot 記録と archive の hash が一致 → tainted=false、games_recorded が進む
state "$A" "$B" ABBA 1; _ab_select_arm >/dev/null   # B
cp alt.py strategy.py.game_snapshot
printf '{"turn":1,"strategy_hash":"%s"}\n' "$B" > arch.jsonl
_ab_record_game 1234 1300 88 arch.jsonl >/dev/null
n=$(_ab_state_get games_recorded); [ "$n" = "2" ] && ok || ng "games_recorded after record = $n"
python3 -c "import json;r=json.loads(open('tmp/state/ab_games.jsonl').readlines()[-1]);assert r['arm']=='B' and r['tainted'] is False and r['eval']==1300.0 and r['idx']==1,r" && ok || ng "record content"
# 不一致 → tainted
cp strategy.py strategy.py.game_snapshot   # runner が root を打ってしまった
_ab_record_game 10 10 5 arch.jsonl >/dev/null
python3 -c "import json;r=json.loads(open('tmp/state/ab_games.jsonl').readlines()[-1]);assert r['tainted'] is True,r" && ok || ng "tainted detection"
# 未記録 (rc75) では counter が進まない = 同じ腕を打ち直す
state "$A" "$B" ABBA 2; _ab_select_arm >/dev/null; a1=$AB_ARM; _ab_select_arm >/dev/null; [ "$a1" = "$AB_ARM" ] && ok || ng "unrecorded game replays the same arm"
# AB_ARM 空なら記録しない
AB_ARM=""; before=$(wc -l < tmp/state/ab_games.jsonl); _ab_record_game 1 1 1 arch.jsonl >/dev/null; after=$(wc -l < tmp/state/ab_games.jsonl); [ "$before" = "$after" ] && ok || ng "no record without arm"

# 6) abort → 不活性、理由が state に残る
_ab_abort "decide_exception arm=B" >/dev/null
out=$(_ab_active 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "abort" && ok || ng "abort disables ($out)"
python3 -c "import json;st=json.load(open('tmp/state/ab_state.json'));assert 'decide_exception' in st.get('abort_reason',''),st" && ok || ng "abort reason recorded"
rm -f tmp/state/ab_abort
_ab_is_arm_hash "$A" && _ab_is_arm_hash "$B" && ! _ab_is_arm_hash "0000" && ok || ng "_ab_is_arm_hash"

echo "test_ab_interleave: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
