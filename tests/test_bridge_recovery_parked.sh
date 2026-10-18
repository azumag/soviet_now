#!/bin/bash
# eloop の bridge 自己復旧が、game-only lifecycle で意図的に停止中のブリッジを
# 復活させないことを固定する (soviet_now#599)。実 kill/tmux/lease には触れない。
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/bridge-recovery-parked.XXXXXX")"
trap 'rm -rf "$test_root"' EXIT

cd "$test_root"
mkdir -p tmp/state lifecycle
log() { :; }
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
source "$repo_root/lib/game_lifecycle.sh"
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
source "$repo_root/lib/bridge_recovery.sh"

calls="$test_root/calls"
: >"$calls"
# parked でない経路が lease に到達したことだけを観測する (その先へは進めない)。
rr_lease_acquire() { echo lease >>"$calls"; return 1; }
rr_lease_release() { :; }
tmux() { echo "tmux $*" >>"$calls"; }
kill() { echo "kill $*" >>"$calls"; }

write_pair() {
	local status="$1" deadline="$2"
	python3 - "$GAME_LIFECYCLE_DIR" "$status" "$deadline" <<'PY'
import json, sys
d, status, deadline = sys.argv[1:4]
base = {"schema": 1, "request_id": "r1", "game": "sorengame", "generation": 603,
        "deadline_epoch": float(deadline), "deadline_at": "x"}
json.dump(base, open(f"{d}/request.json", "w"))
json.dump({**base, "status": status}, open(f"{d}/ack.json", "w"))
PY
}
expect_rc() {
	local want="$1" label="$2"; shift 2
	local rc=0
	"$@" || rc=$?
	if [ "$rc" -ne "$want" ]; then
		echo "FAIL $label: rc=$rc want=$want" >&2
		exit 1
	fi
}

now=$(date +%s)
future=$((now + 600))

# 1) 停止済み(terminal)なら _br_relaunch は何もせず rc=3。lease/kill/tmux に触れない。
write_pair stopped "$future"
: >"$calls"
expect_rc 3 "relaunch while stopped" _br_relaunch
[ ! -s "$calls" ] || { echo "FAIL: parked relaunch touched resources: $(cat "$calls")" >&2; exit 1; }

# 2) 停止中(非terminal・期限内)も同様。
write_pair stopping "$future"
: >"$calls"
expect_rc 3 "relaunch while stopping" _br_relaunch
[ ! -s "$calls" ]

# 3) _ensure_bridge_alive は復旧未完了(1)を返し、試合開始を延期させる。
: >"$calls"
expect_rc 1 "ensure while stopped" _ensure_bridge_alive
[ ! -s "$calls" ]

# 4) 回帰: park していなければ従来どおり lease まで進む(ガードが過剰に効かない)。
rm -f "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json"
: >"$calls"
expect_rc 2 "relaunch when not parked reaches lease" _br_relaunch
grep -qx lease "$calls"

# 5) 別世代の ack は park にならない (古い要求で復旧を永久に止めない)。
write_pair stopped "$future"
python3 - "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json, sys
p = sys.argv[1]; v = json.load(open(p)); v["generation"] = 999; json.dump(v, open(p, "w"))
PY
: >"$calls"
expect_rc 2 "mismatched ack must not park" _br_relaunch
grep -qx lease "$calls"

echo "ok: bridge recovery honors lifecycle park"
