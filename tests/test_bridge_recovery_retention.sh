#!/bin/bash
# 同一盤面保持要求中の stale 判定が bridge を kill/relaunch して盤面を失わない
# ことを固定する (soviet_now#500)。
#
# 2026-09-23 の事象: 建国演出を終局と誤認して停止した後、所有確認済み lifecycle
# 要求の cancel で休止を解除 → supervisor が soren_loop を再起動 →
# _ensure_bridge_alive が `game_state停滞(806s)` を検知して bridge を
# kill/relaunch → runner が score0 から開始し、MOVE/score6111/makeSorenCount1 の
# 盤面を失った。依頼は盤面保持だった。
#
# 再現の要点: game_state.json は stateChanged のときだけ書き換わる
# (soviet_local.mjs の writeGameState) ため、MOVE を送っていない生きた盤面は
# mtime が何分止まっても正常である。mtime 停滞だけで kill/relaunch へ進むと
# 保持対象の盤面が破棄される。
#
# 実 kill/tmux/lease/curl には触れない。観測だけですべての経路を判定する。
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/bridge-recovery-retention.XXXXXX")"
trap 'rm -rf "$test_root"' EXIT

cd "$test_root"
mkdir -p tmp/state lifecycle tmp/soviet_local_chromium_profile
log() { :; }
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
source "$repo_root/lib/game_lifecycle.sh"
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
BRIDGE_OBSERVE_WAIT_SEC=0
source "$repo_root/lib/bridge_recovery.sh"

calls="$test_root/calls"
: >"$calls"
# 復旧が実際の破壊操作へ到達したことだけを観測する (その先へは進めない)。
rr_lease_acquire() { echo lease >>"$calls"; return 1; }
rr_lease_release() { :; }
tmux() { echo "tmux $*" >>"$calls"; }
kill() { echo "kill $*" >>"$calls"; }
# 生きた page の非破壊再観測。デフォルトは「盤面が生きている」。
_br_live_page_reobservable() { echo probe >>"$calls"; return "${LIVE_PAGE_RC:-0}"; }
# bridge プロセスは生存している (事象当時も PID は残っていた)。
_br_target_pids() { echo 4242; }

now=$(date +%s)
future=$((now + 600))

# request/ack ペアを書く。status と ack の経過秒数を指定できる。
write_pair() {
	local status="$1" age="$2"
	python3 - "$GAME_LIFECYCLE_DIR" "$status" "$age" <<'PY'
import json
import sys
import time
from datetime import datetime, timedelta, timezone

d, status, age = sys.argv[1:4]
now = time.time()
base = {"schema": 1, "request_id": "r1", "game": "sorengame", "generation": 500,
        "deadline_epoch": now + 600, "deadline_at": "x"}
json.dump(base, open(f"{d}/request.json", "w"))
ack = {**base, "status": status,
       "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=int(age))).isoformat().replace("+00:00", "Z")}
json.dump(ack, open(f"{d}/ack.json", "w"))
PY
}

# game_state.json を「生きているが動いていない盤面」にする。
# score/makeSorenCount は事象当時の値 (MOVE, score6111, makeSorenCount1)。
write_live_board() {
	local age="$1"
	python3 - "$test_root" "$age" <<'PY'
import json
import os
import sys
import time

root, age = sys.argv[1], int(sys.argv[2])
state = {"state": "MOVE", "score": 6111, "makeSorenCount": 1, "pieces": [1, 2, 3]}
path = os.path.join(root, "game_state.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(state, f)
# stale 判定閾値 (BRIDGE_STALE_SEC=240) を十分に超えた mtime を作る。
old = time.time() - age
os.utime(path, (old, old))
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

# 復旧 backoff は関数内の shell 変数で持ち回るため、ケースをまたぐと cooldown で
# lease に到達しなくなる。各ケースの前に reset して独立に判定する。
reset_backoff() {
	_BR_CONSEC_FAIL=0
	_BR_LAST_ATTEMPT=0
}

# 破壊操作 (kill/tmux/lease) が一度も呼ばれていないことを確認する。
# probe は読み取り専用の再観測なので許容する。
assert_no_destructive_call() {
	local label="$1"
	if grep -qE '^(kill|tmux|lease)' "$calls"; then
		echo "FAIL $label: destructive recovery ran: $(grep -E '^(kill|tmux|lease)' "$calls" | tr '\n' ' ')" >&2
		exit 1
	fi
}

# 1) 事象の再現: cancel 後の旧ゲーム継続 + game_state 停滞 + 生きた page。
#    → 復旧扱い (0) を返し、kill/relaunch/tmux は 0 回。
write_pair cancelled 30
write_live_board 806
: >"$calls"
reset_backoff; expect_rc 1 "cancelled retention keeps the board" _ensure_bridge_alive
assert_no_destructive_call "cancelled retention keeps the board"

# 2) 同じ条件で _br_relaunch を直接呼んでも kill/tmux は 0 回 (rc=3=保留)。
: >"$calls"
expect_rc 3 "relaunch held during retention" _br_relaunch
assert_no_destructive_call "relaunch held during retention"

# 3) boundary 待ち (試合終了境界を待機中) でも保持。これは #498 側の直接原因を
#    復旧操作が盤面ごと破棄しないための経路。
write_pair boundary 30
write_live_board 500
: >"$calls"
reset_backoff; expect_rc 1 "boundary wait keeps the board" _ensure_bridge_alive
assert_no_destructive_call "boundary wait keeps the board"

# 4) player_change prepared でも保持 (次ゲームのみ保留中の現盤面)。
write_pair prepared 30
write_live_board 500
: >"$calls"
reset_backoff; expect_rc 1 "prepared keeps the board" _ensure_bridge_alive
assert_no_destructive_call "prepared keeps the board"

# 5) 生きた page を再観測できない場合は復旧不能を明示し、kill/relaunch も
#    盤面リセットも行わず試合開始だけを延期する (rc=1)。
write_pair cancelled 30
write_live_board 806
: >"$calls"
reset_backoff; LIVE_PAGE_RC=1 expect_rc 1 "unreobservable page defers without reset" _ensure_bridge_alive
assert_no_destructive_call "unreobservable page defers without reset"

# 6) 回帰: 保持要求が無ければ従来どおり mtime 停滞は復旧事象 (lease まで進む)。
rm -f "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json"
write_live_board 806
: >"$calls"
reset_backoff; expect_rc 1 "stale without retention still recovers" _ensure_bridge_alive
grep -qx lease "$calls"

# 7) 回帰: 保持窓 (GAME_LIFECYCLE_BOARD_RETENTION_SEC) を過ぎた要求は抑止しない。
#    古い ack が復旧を永久に止めないことを保証する。
write_pair cancelled 99999
write_live_board 806
: >"$calls"
reset_backoff; expect_rc 1 "expired retention window must not suppress recovery" _ensure_bridge_alive
grep -qx lease "$calls"

# 8) 回帰: 別世代の ack は保持要求にならない (古い要求で新盤面の復旧を止めない)。
write_pair cancelled 30
python3 - "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys
p = sys.argv[1]
v = json.load(open(p))
v["generation"] = 999
json.dump(v, open(p, "w"))
PY
: >"$calls"
reset_backoff; expect_rc 1 "mismatched ack must not retain" _ensure_bridge_alive
grep -qx lease "$calls"

# 9) 回帰: 期限切れの非終端 status (boundary) は抑止しない。
write_pair boundary 30
python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys
import time
for p in sys.argv[1:]:
    v = json.load(open(p))
    v["deadline_epoch"] = time.time() - 1
    json.dump(v, open(p, "w"))
PY
: >"$calls"
reset_backoff; expect_rc 1 "expired boundary must not retain" _ensure_bridge_alive
grep -qx lease "$calls"

# 10) 回帰: stopped は意図的停止 (parked) の管轄。保持要求としては扱わず、
#     既存の park ガードが rc=3 で応じる (重複ガードで挙動を変えない)。
write_pair stopped 30
write_live_board 806
: >"$calls"
expect_rc 3 "stopped stays on the parked path" _br_relaunch
assert_no_destructive_call "stopped stays on the parked path"

# 11) 回帰: 動いている盤面 (mtime fresh) は従来どおり即 normal。
rm -f "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json"
write_live_board 5
: >"$calls"
reset_backoff; expect_rc 0 "fresh state stays normal" _ensure_bridge_alive
assert_no_destructive_call "fresh state stays normal"

# 12) 回帰: bridge プロセス消失は保持要求中でも復旧事象 (盤面は既に失われている
#     ので、ここで kill/relaunch を止めると復旧経路がなくなる)。
write_pair cancelled 30
write_live_board 806
_br_target_pids() { :; }
: >"$calls"
reset_backoff; expect_rc 1 "process loss still recovers under retention" _ensure_bridge_alive
grep -qx lease "$calls"

# 13) A current live observation must resume play even after a retained cancel.
# Returning 1 here would make play_one_game defer forever until retention expires.
_br_target_pids() { echo 4242; }
write_pair cancelled 30
write_live_board 806
_BR_OBSERVATION="$test_root/observed.json"
printf '%s\n' '{"schema":1,"board":{"state":"MOVE","score":6111}}' >"$_BR_OBSERVATION"
: >"$calls"
reset_backoff; expect_rc 0 "fresh observation allows retained game to continue" _ensure_bridge_alive
assert_no_destructive_call "fresh observation allows retained game to continue"

echo "ok: bridge recovery honors same-board retention (soviet_now#500)"
