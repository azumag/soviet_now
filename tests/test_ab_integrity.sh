#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_integrity_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tmp/history"
cp "$ROOT/extract_decide_hash.py" "$WORK/"
cp "$ROOT/strategy.py" "$WORK/strategy.py"
python3 - "$ROOT/strategy.py" "$WORK/alt.py" "$WORK/newroot.py" <<'PY'
import sys
src=open(sys.argv[1],encoding='utf-8').read()
old='best_x = 0.0905'
assert src.count(old)==1, src.count(old)
open(sys.argv[2],'w',encoding='utf-8').write(src.replace(old,'best_x = 0.0906'))
open(sys.argv[3],'w',encoding='utf-8').write(src.replace(old,'best_x = 0.0907'))
PY
cd "$WORK"
A=$(python3 extract_decide_hash.py strategy.py)
B=$(python3 extract_decide_hash.py alt.py)
N=$(python3 extract_decide_hash.py newroot.py)
[ "$A" != "$B" ] && [ "$A" != "$N" ] || { echo "FAIL: test hashes must differ"; exit 1; }

log() { echo "[log] $*"; }
TMP_STATE_DIR=tmp/state
AB_HISTORY_DIR=tmp/history
AB_HISTORY_FILE=tmp/history/ab_history.jsonl
STRATEGY_FILE=strategy.py
IMPROVE_LOCK_FILE=tmp/improve.lock
AB_ALT_HELPERS_DIR=tmp/state/ab_alt_helpers
source "$ROOT/strategy/ab_interleave.sh"

cat > set_toggle.sh <<'SH'
#!/usr/bin/env bash
set -u
kv="$1"; key="${kv%%=*}"; value="${kv#*=}"
tmp=.env.tmp
{ grep -Ev "^${key}=" .env 2>/dev/null || true; printf '%s=%s\n' "$key" "$value"; } > "$tmp"
mv "$tmp" .env
SH
chmod +x set_toggle.sh

source "$ROOT/strategy/ab_integrity.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }
state() {
	printf '{"a_hash":"%s","b_hash":"%s","pattern":"ABBA","games_recorded":2,"regression_disabled_before":"0"}\n' "$1" "$2" > tmp/state/ab_state.json
}
prepare() {
	rm -rf tmp/state tmp/history
	mkdir -p tmp/state tmp/history
	cp alt.py tmp/state/ab_alt_strategy.py
	state "$A" "$B"
	printf 'REGRESSION_DISABLED=1\nSOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py\n' > .env
	export SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py
	touch tmp/state/improve_daemon.paused
	printf '{"idx":0,"arm":"A","hash":"%s","tainted":false}\n' "$A" > tmp/state/ab_games.jsonl
}
run_active_capture() {
	local rc
	_ab_active > ab_active.out 2>&1
	rc=$?
	cat ab_active.out
	return "$rc"
}

# 1) 正常な A/B は退役させない。
prepare
_ab_active >/dev/null 2>&1 && ok || ng "healthy experiment unexpectedly inactive"
[ -f tmp/state/ab_state.json ] && ok || ng "healthy state was retired"

# 2) root(A) が deploy/restore 等で変わったら勝敗を付けず stale として即退役する。
prepare
cp newroot.py strategy.py
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
[ "$rc" -ne 0 ] && echo "$out" | grep -q "stale experiment retired" && ok || ng "root drift was not retired ($rc: $out)"
[ ! -f tmp/state/ab_state.json ] && ok || ng "stale base state still active"
[ ! -f tmp/state/ab_games.jsonl ] && ok || ng "stale games still active"
ls tmp/history/ab_*_state_stale.json >/dev/null 2>&1 && ok || ng "stale state archive missing"
ls tmp/history/ab_*_games_stale.jsonl >/dev/null 2>&1 && ok || ng "stale games archive missing"
python3 - <<PY && ok || ng "stale state metadata missing"
import glob,json
p=glob.glob('tmp/history/ab_*_state_stale.json')[-1]
st=json.load(open(p))
assert st.get('retired_reason')=='stale_base', st
assert st.get('observed_hash')=='$N', st
assert st.get('expected_hash')=='$A', st
assert 'winner' not in st, st
PY
[ "$(python3 extract_decide_hash.py strategy.py)" = "$N" ] && ok || ng "retirement changed current root"
grep -q '^REGRESSION_DISABLED=0$' .env && grep -q '^SOREN_AB_ALT_STRATEGY=$' .env && ok || ng "runtime toggles not restored"
[ -z "${SOREN_AB_ALT_STRATEGY:-}" ] && ok || ng "long-lived shell kept alt strategy"

# 3) B 実体が state.b_hash と変わった場合も stale。B は rejected 扱いにしない。
cp "$ROOT/strategy.py" strategy.py
A=$(python3 extract_decide_hash.py strategy.py)
prepare
cp newroot.py tmp/state/ab_alt_strategy.py
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
[ "$rc" -ne 0 ] && echo "$out" | grep -q "stale_candidate" && ok || ng "candidate drift was not retired ($rc: $out)"
[ ! -f tmp/state/ab_state.json ] && ok || ng "stale candidate state still active"
[ ! -e tmp/state/rejected_hashes.txt ] && ok || ng "invalid experiment must not reject B"
python3 - <<'PY' && ok || ng "candidate stale metadata missing"
import glob,json
p=glob.glob('tmp/history/ab_*_state_stale.json')[-1]
st=json.load(open(p))
assert st.get('retired_reason')=='stale_candidate', st
assert 'winner' not in st, st
PY

# 4) state が残ったまま B 実体が消えた部分退役/破損も、active 表示を残さず閉じる。
prepare
rm -f tmp/state/ab_alt_strategy.py
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
[ "$rc" -ne 0 ] && echo "$out" | grep -q "stale_candidate_missing" && ok || ng "missing candidate was not retired ($rc: $out)"
[ ! -f tmp/state/ab_state.json ] && ok || ng "missing candidate left active state"
ls tmp/history/ab_*_state_stale.json >/dev/null 2>&1 && ok || ng "missing candidate state archive missing"

# 5) lifecycle lock である state に期待 hash が欠けている場合は再開不能なので stale とする。
prepare
state "$A" ""
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
[ "$rc" -ne 0 ] && echo "$out" | grep -q "stale_state_b_hash_missing" && ok || ng "missing state hash was not retired ($rc: $out)"
[ ! -f tmp/state/ab_state.json ] && ok || ng "malformed state remained active"

# 6) 実体 hash 計算失敗は一時的な I/O/parse failure の可能性があるため、証拠を消さず fail-closed のままにする。
prepare
chmod 000 strategy.py
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
chmod 644 strategy.py
[ "$rc" -ne 0 ] && ok || ng "unhashable root unexpectedly active"
[ -f tmp/state/ab_state.json ] && ok || ng "transient root hash failure retired evidence"

echo "test_ab_integrity: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
