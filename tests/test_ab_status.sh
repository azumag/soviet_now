#!/usr/bin/env bash
# strategy/ab_status.sh の単体テスト: status build → publish (内容変更時のみ push)
# → runtime/ab-state ブランチの内容 → 無効化/clone 不在時の no-op。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_status_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tmp/history"
cp "$ROOT/extract_decide_hash.py" "$WORK/"
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cd "$WORK"

# publish 先: bare remote + persist clone 相当の管理 clone
git init --bare remote.git >/dev/null 2>&1
git init repo >/dev/null 2>&1
git -C repo config user.email "test@example.com"
git -C repo config user.name "test"
git -C repo remote add origin "$WORK/remote.git"
printf 'x\n' > repo/seed.txt
git -C repo add seed.txt && git -C repo commit -qm seed && git -C repo branch -M main
git -C repo push -q origin main
git -C repo fetch -q origin

log() { echo "[log] $*"; }
TMP_STATE_DIR=tmp/state
AB_HISTORY_DIR=tmp/history
AB_HISTORY_FILE=tmp/history/ab_history.jsonl
STRATEGY_FILE=strategy.py
IMPROVE_LOCK_FILE=tmp/improve.lock
AB_ALT_HELPERS_DIR=tmp/state/ab_alt_helpers
SOREN_PERSIST_REPO="$WORK/repo"
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
source "$ROOT/strategy/ab_status.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

# 1) 実験なし → active=false の status を build できる。
key1=$(_ab_status_build tmp/state/ab_status.json)
[ -f tmp/state/ab_status.json ] && ok || ng "status file missing"
python3 - <<'PY' && ok || ng "status schema"
import json
d=json.load(open("tmp/state/ab_status.json"))
assert d["schema_version"]==1, d
assert d["active"] is False, d
assert d["root_matches_a"] is None, d
assert "observed_epoch" in d and "observed_at" in d, d
PY
[ -n "$key1" ] && ok || ng "content key empty"

# 2) clone が無い環境では publish は no-op (0)。
SOREN_PERSIST_REPO="$WORK/no-such-dir"
_ab_status_publish "test" >/dev/null 2>&1 && ok || ng "publish should no-op without clone"
[ ! -f tmp/state/ab_status_publish.json ] || {
	python3 - <<'PY' && ok || ng "publish state without clone"
import json
d=json.load(open("tmp/state/ab_status_publish.json"))
assert d.get("commit","x")=="" and d.get("last_error")=="", d
PY
}

# 3) clone あり → publish で runtime/ab-state へ push される。
SOREN_PERSIST_REPO="$WORK/repo"
_ab_status_publish "test" >/dev/null 2>&1 && ok || ng "publish failed"
git -C repo fetch -q origin runtime/ab-state
git -C repo show origin/runtime/ab-state:ab_status.json >/dev/null 2>&1 && ok || ng "status file not on branch"
python3 - <<'PY' && ok || ng "published content"
import json,subprocess
raw=subprocess.check_output(["git","-C","repo","show","origin/runtime/ab-state:ab_status.json"],text=True)
d=json.loads(raw)
assert d["active"] is False, d
PY
python3 - <<'PY' && ok || ng "publish state recorded"
import json
d=json.load(open("tmp/state/ab_status_publish.json"))
assert d.get("commit"), d
assert d.get("last_error")=="", d
PY

# 4) 内容が同じ間は再 push しない (HEAD が進まない)。
head1=$(git -C repo rev-parse origin/runtime/ab-state)
_ab_status_publish "test2" >/dev/null 2>&1
head2=$(git -C repo rev-parse origin/runtime/ab-state)
[ "$head1" = "$head2" ] && ok || ng "unchanged content pushed again"

# 5) 実験 state を置くと active=true になり、内容変更で push が走る。
A=$(python3 extract_decide_hash.py strategy.py)
printf '{"a_hash":"%s","b_hash":"deadbeef0001","pattern":"ABBA","games_recorded":4,"confirmation_required":true,"confirmation_phase":"confirmation","started_at":"2026-11-15T00:00:00","primary":"eval"}\n' "$A" > tmp/state/ab_state.json
_ab_status_publish "state" >/dev/null 2>&1
head3=$(git -C repo rev-parse origin/runtime/ab-state)
[ "$head3" != "$head2" ] && ok || ng "changed content did not push"
python3 - <<PY && ok || ng "published active status"
import json,subprocess
raw=subprocess.check_output(["git","-C","repo","show","origin/runtime/ab-state:ab_status.json"],text=True)
d=json.loads(raw)
assert d["active"] is True, d
assert d["a_hash"]=="$A", d
assert d["confirmation_phase"]=="confirmation", d
assert d["games_recorded"]==4, d
assert d["root_matches_a"] is True, d
PY

# 6) AB_STATUS_PUBLISH_ENABLED=0 で無効化できる。
printf 'AB_STATUS_PUBLISH_ENABLED=0\n' > .env
rm tmp/state/ab_state.json
_ab_status_publish "off" >/dev/null 2>&1
head4=$(git -C repo rev-parse origin/runtime/ab-state)
[ "$head4" = "$head3" ] && ok || ng "disabled publish still pushed"

# 7) heartbeat 経過で同一内容でも push される。
printf 'AB_STATUS_PUBLISH_ENABLED=1\n' > .env
python3 - <<'PY'
import json
p="tmp/state/ab_status_publish.json"
d=json.load(open(p)); d["published_at"]=1
open(p,"w").write(json.dumps(d))
PY
_ab_status_publish "heartbeat" >/dev/null 2>&1
head5=$(git -C repo rev-parse origin/runtime/ab-state)
[ "$head5" != "$head4" ] && ok || ng "heartbeat did not push"

echo "test_ab_status: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
