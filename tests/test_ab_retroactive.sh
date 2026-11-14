#!/usr/bin/env bash
# strategy/ab_retroactive.sh の単体テスト: stale 退役実験の一覧・詳細・
# 後追い A/B 開始 (start-stale) と retroactive 証跡 (soviet_now#865)。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_retro_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tmp/history" "$WORK/tools" "$WORK/lib" "$WORK/strategy_helpers" "$WORK/strategy_versions/by_hash" "$WORK/strategy_versions_archive/by_hash"
cp "$ROOT/extract_decide_hash.py" "$WORK/"
cp "$ROOT/set_toggle.sh" "$WORK/"
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cp "$ROOT/tools/policy_bundle.py" "$ROOT/tools/ab_report.py" "$ROOT/tools/ab_decide.py" "$WORK/tools/" 2>/dev/null || true
cp "$ROOT/strategy_helpers/"*.py "$WORK/strategy_helpers/" 2>/dev/null || true
cp "$ROOT/lib/eval_stats.py" "$WORK/lib/" 2>/dev/null || true
python3 - "$ROOT/strategy.py" "$WORK/alt.py" <<'PY'
import sys
src=open(sys.argv[1],encoding='utf-8').read()
old='best_x = 0.0905'
assert src.count(old)==1, src.count(old)
open(sys.argv[2],'w',encoding='utf-8').write(src.replace(old,'best_x = 0.0906'))
PY
cd "$WORK"
A=$(python3 extract_decide_hash.py strategy.py)
B=$(python3 extract_decide_hash.py alt.py)
[ "$A" != "$B" ] || { echo "FAIL: test hashes must differ"; exit 1; }

log() { echo "[log] $*"; }
TMP_STATE_DIR=tmp/state
AB_HISTORY_DIR=tmp/history
AB_HISTORY_FILE=tmp/history/ab_history.jsonl
STRATEGY_FILE=strategy.py
IMPROVE_LOCK_FILE=tmp/improve.lock
AB_ALT_HELPERS_DIR=tmp/state/ab_alt_helpers
STRATEGY_HASH_ARCHIVE_DIR=strategy_versions/by_hash
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR=strategy_versions_archive/by_hash
GAME_COUNT_FILE=game_count.txt
REJECTED_HASHES_FILE=tmp/state/rejected_hashes.txt
echo 7 > game_count.txt
source "$ROOT/lib/strategy_archive.sh"
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
# adopt 経路は戦略 runtime の apply helpers を要求する (test_ab_gate.sh と同じ方針)。
source "$ROOT/core/strategy_runtime.sh"
IMPROVE_LIVE_APPLY_POLICY=off
source "$ROOT/strategy/ab_retroactive.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

# 退役実験の fixture: 108 試合 confirmation で退役した実験。
TS=20261115_010600
printf '{"a_hash":"%s","b_hash":"%s","pattern":"ABBA","games_recorded":108,"retired_reason":"stale_base","confirmation_phase":"confirmation","observed_hash":"deadbeef9999","retired_at":"2026-11-15T01:06:00"}\n' "$A" "$B" > "tmp/history/ab_${TS}_state_stale.json"
cp alt.py "tmp/history/ab_${TS}_alt_stale.py"
printf '{"idx":0,"arm":"A","hash":"%s","tainted":false}\n' "$A" > "tmp/history/ab_${TS}_games_stale.jsonl"

# 1) list が退役実験を拾う。
out=$(_ab_retro_list)
echo "$out" | grep -q "$TS" && ok || ng "list missed stale experiment"
echo "$out" | grep -q "stale_base" && ok || ng "list missed retired_reason"

# 2) show が詳細と次コマンドを出す。
out=$(_ab_retro_show "$TS")
echo "$out" | grep -q "start-stale" && ok || ng "show lacks start-stale hint"
echo "$out" | grep -q "$B" && ok || ng "show lacks b_hash"

# 3) 実験走行中は start-stale を拒否する。
printf '{"a_hash":"%s","b_hash":"%s","pattern":"ABBA"}\n' "$A" "$B" > tmp/state/ab_state.json
_ab_retro_start_stale "$TS" >/dev/null 2>&1 && ng "start-stale ran during active experiment" || ok
rm -f tmp/state/ab_state.json

# 4) start-stale が alt 実体から A/B を開始し retroactive 証跡を残す。
_ab_retro_start_stale "$TS" > start.out 2>&1; rc=$?
if [ "$rc" -eq 0 ] && [ -f tmp/state/ab_state.json ]; then
	python3 - <<PY && ok || ng "retroactive provenance missing"
import json
st=json.load(open('tmp/state/ab_state.json'))
r=st.get('retroactive')
assert r, st
assert r.get('retro_for_ts')=='$TS', r
assert r.get('original_b_hash')=='$B', r
assert r.get('retired_reason')=='stale_base', r
assert r.get('requested_by')=='ab_retroactive', r
PY
	[ "$(python3 extract_decide_hash.py tmp/state/ab_alt_strategy.py)" = "$B" ] && ok || ng "alt arm hash mismatch"
else
	cat start.out
	ng "start-stale failed (rc=$rc)"
fi

# 5) 未知の ts は失敗する。
_ab_retro_start_stale 29990101_000000 >/dev/null 2>&1 && ng "unknown ts unexpectedly started" || ok
_ab_retro_show 29990101_000000 >/dev/null 2>&1 && ng "unknown ts show succeeded" || ok

# 6) prev-roots: フル hash を出し、--repo は origin/main を見る。
mkdir -p gitsrc && cd gitsrc
git init -q && git config user.email t@e && git config user.name t
cp "$ROOT/extract_decide_hash.py" .
cp "$ROOT/strategy.py" strategy.py
git add strategy.py && git commit -qm "v1"
python3 - "$ROOT/strategy.py" strategy.py <<'PY'
import sys
s=open(sys.argv[1]).read(); open(sys.argv[2],'w').write(s.replace('best_x = 0.0905','best_x = 0.0908'))
PY
git add strategy.py && git commit -qm "v2"
git branch -M main
cd ..
git init -q --bare gitsrc-remote.git
git -C gitsrc remote add origin "$WORK/gitsrc-remote.git"
git -C gitsrc push -q origin main
# --repo の clone: worktree が別ブランチに居ても origin/main の履歴を見る。
git clone -q gitsrc-remote.git mgmt 2>/dev/null
git -C mgmt checkout -qb someother 2>/dev/null
out=$(_ab_retro_prev_roots 5 --repo mgmt)
# decide hash は 12 hex (extract_decide_hash.py の hexdigest[:12])。
# archive のファイル名もこれなのでそのまま `start --b` へ渡せる。
echo "$out" | grep -q "^[0-9a-f]\{12\}" && ok || ng "prev-roots hash not full length"
N1=$(python3 - <<'PY'
import subprocess
h=subprocess.run(["python3","extract_decide_hash.py","mgmt/strategy.py"],capture_output=True,text=True).stdout.strip()
print(h)
PY
)
echo "$out" | grep -q "$N1" && ok || ng "prev-roots missed origin/main head hash"
( cd gitsrc && _ab_retro_prev_roots 1 ) | head -1 | grep -q "^[0-9a-f]\{12\}" && ok || ng "prev-roots default path"

# 7) stale_base_b_merged の show が元 A との比較コマンドを案内する。
TS2=20261116_010000
printf '{"a_hash":"%s","b_hash":"%s","retired_reason":"stale_base_b_merged","observed_hash":"%s"}\n' "$A" "$B" "$B" > "tmp/history/ab_${TS2}_state_stale.json"
out=$(_ab_retro_show "$TS2")
echo "$out" | grep -q "start --b" && ok || ng "b_merged show lacks old-A hint"
echo "$out" | grep -q "a_hash" && ok || ng "b_merged show lacks a_hash"

echo "test_ab_retroactive: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
