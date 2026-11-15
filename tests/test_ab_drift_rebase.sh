#!/usr/bin/env bash
# AB_ON_ROOT_DRIFT=rebase: root drift で A を新 root へ張り替え、旧標本を保全し
# 測定を selection からやり直す (soviet_now#865)。既定 (retire) の挙動は
# test_ab_integrity.sh が担保する。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_rebase_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tmp/history" "$WORK/tools"
cp "$ROOT/extract_decide_hash.py" "$WORK/"
cp "$ROOT/tools/policy_bundle.py" "$WORK/tools/"
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cp -r "$ROOT/strategy_helpers" "$WORK/" 2>/dev/null || true
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
reload_runtime_toggles_force() { :; }
_ab_finish() { rm -f "$AB_STATE_FILE"; return 0; }
source "$ROOT/strategy/ab_integrity.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

prepare_confirmation() {
	rm -rf tmp/state tmp/history .env
	mkdir -p tmp/state tmp/history
	cp alt.py tmp/state/ab_alt_strategy.py
	printf '{"a_hash":"%s","b_hash":"%s","pattern":"ABBA","games_recorded":108,"regression_disabled_before":"0","confirmation_required":true,"confirmation_phase":"confirmation","verified_improvement":true,"selection_finished_at":"2026-11-14T00:00:00","selection_reason":"adopt","selection_games":148,"selection_games_file":"ab_20261114_000000_selection_games.jsonl"}\n' "$A" "$B" > tmp/state/ab_state.json
	printf 'REGRESSION_DISABLED=1\nSOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py\nAB_ON_ROOT_DRIFT=rebase\n' > .env
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

# 1) rebase policy: confirmation 中の root drift で実験を残し A を新 root へ張り替える。
prepare_confirmation
cp newroot.py strategy.py
run_active_capture >/dev/null 2>&1; rc=$?; out=$(cat ab_active.out)
[ "$rc" -ne 0 ] && echo "$out" | grep -q "rebase" && ok || ng "rebase did not run ($rc: $out)"
[ -f tmp/state/ab_state.json ] && ok || ng "experiment state was retired under rebase"
python3 - <<PY && ok || ng "rebase state fields"
import json
st=json.load(open('tmp/state/ab_state.json'))
assert st['a_hash']=='$N', st
assert st['b_hash']=='$B', st
assert st['games_recorded']==0, st
assert st['confirmation_phase']=='selection', st
assert st['verified_improvement'] is False, st
assert 'selection_games' not in st, st
assert 'selection_games_file' not in st, st
evs=st.get('root_drift_events')
assert isinstance(evs,list) and len(evs)==1, st
ev=evs[0]
assert ev['from']=='$A' and ev['to']=='$N' and ev['games']==108, ev
assert ev['phase']=='confirmation', ev
assert ev['archived_games'].endswith('_games_rootdrift.jsonl'), ev
PY
[ ! -f tmp/state/ab_games.jsonl ] || [ ! -s tmp/state/ab_games.jsonl ] && ok || ng "live games ledger not reset"
ls tmp/history/ab_*_games_rootdrift.jsonl >/dev/null 2>&1 && ok || ng "old samples not archived"
ls tmp/history/ab_*_state_stale.json 2>/dev/null | grep -q . && ng "rebase left stale archive" || ok
[ -f tmp/state/ab_alt_strategy.py ] && ok || ng "alt file lost under rebase"
grep -q '^REGRESSION_DISABLED=1$' .env && ok || ng "regression toggle unexpectedly reset"

# 2) 再開した実験は新 root を A として active 判定を通る (state 残存で再 drift しない)。
cp newroot.py strategy.py
_ab_active >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok || ng "rebased experiment not active again"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$(python3 - <<'PY'
import json; print(json.load(open('tmp/state/ab_state.json'))['a_hash'])
PY
)" ] && ok || ng "a_hash not re-anchored"

# 3) env 未設定 (既定 retire) では drift は従来どおり stale 退役する。
prepare_confirmation
sed -i '/^AB_ON_ROOT_DRIFT=/d' .env
cp newroot.py strategy.py
run_active_capture >/dev/null 2>&1; rc=$?
[ ! -f tmp/state/ab_state.json ] && ok || ng "default policy rebased instead of retiring"
ls tmp/history/ab_*_state_stale.json >/dev/null 2>&1 && ok || ng "default retire archive missing"

# 4) rebase 自体が失敗した場合は従来どおり stale 退役へ落ちる (dispatch の fallback)。
prepare_confirmation
cp newroot.py strategy.py
_ab_integrity_rebase_root() { return 1; }
run_active_capture >/dev/null 2>&1; rc=$?
[ ! -f tmp/state/ab_state.json ] && ok || ng "failed rebase kept state"
ls tmp/history/ab_*_state_stale.json >/dev/null 2>&1 && ok || ng "failed rebase did not retire stale"

# 5) 観測された root が B と同じ場合 (B が直接マージされた場合)、rebase 方針でも
#    退役する (rebase すると A=B の同一腕実験になり元 A との比較を失う)。
prepare_confirmation
cp alt.py strategy.py   # root を B と同じ内容へ (B の直接マージを模す)
run_active_capture >/dev/null 2>&1; rc=$?
[ ! -f tmp/state/ab_state.json ] && ok || ng "B-merged drift kept state"
python3 - <<'PY' && ok || ng "stale_base_b_merged metadata missing"
import glob,json
p=glob.glob('tmp/history/ab_*_state_stale.json')[-1]
st=json.load(open(p))
assert st.get('retired_reason')=='stale_base_b_merged', st
assert 'winner' not in st, st
PY

echo "test_ab_drift_rebase: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
