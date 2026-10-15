#!/bin/bash
# eloop の bridge 自己復旧が、実観測 (game_observation.json) の裏取りなしに
# 盤面を捨てないことを固定する (soviet_now#500)。
#
# 再現する障害 (2026-09-23): 建国演出を終局と誤認して止まった試合を復旧するため
# 所有確認済み lifecycle 要求を cancel し、supervisor が soren_loop を再開した。
# その最初の周回で _ensure_bridge_alive が game_state.json の mtime 停滞
# (806s) だけを根拠に bridge を kill/relaunch し、保持していた盤面
# (MOVE/score/makeSorenCount) を失った。mtime は盤面が変化した時だけ進むため、
# 休止/境界待ちの静止は障害ではない。実 kill/tmux/lease/入力には触れない。
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/bridge-recovery-stale.XXXXXX")"
trap 'rm -rf "$test_root"' EXIT

cd "$test_root"
mkdir -p tmp/state lifecycle
log() { printf '%s\n' "$*" >>"$test_root/bridge.log"; }

GAME_LIFECYCLE_DIR="$test_root/lifecycle"
GAME_LIFECYCLE_ENABLED=1
source "$repo_root/lib/game_lifecycle.sh"
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
BRIDGE_OBSERVATION_FILE="$test_root/tmp/state/game_observation.json"
BRIDGE_OBSERVE_FRESH_SEC=15
BRIDGE_OBSERVE_WAIT_SEC=1
# 保留ログの絞り込みは本番既定 (60s) のままだと検証が不安定なので無効化する。
BRIDGE_STALE_NOTICE_SEC=0
source "$repo_root/lib/bridge_recovery.sh"
_BR_GAME_STATE="$test_root/game_state.json"

calls="$test_root/calls"
: >"$calls"
record() { printf '%s\n' "$*" >>"$calls"; }

# 破壊的な復旧操作 (lease/kill/tmux/relaunch) を観測する。bridge への入力
# (retry/reload) は復旧経路からは送られない契約なので、ここに現れたら失敗。
rr_lease_acquire() { record lease; return 1; }
rr_lease_release() { :; }
_br_relaunch() { record relaunch; return 0; }
tmux() { record "tmux $*"; }
kill() { record "kill $*"; }
# 稼働中の bridge (cwd 一致の node soviet_local.mjs) と port/CDP の生存。
_br_target_pids() { echo 4242; }
_br_port_pid() { echo 4242; }
_br_cdp_port_pid() { echo 4242; }
_br_fatal_in_log() { return 1; }
_br_audio_stuck_reason() { :; }
_br_audio_sink_status() { echo NA; }

board='{"state":"MOVE","score":6111,"makeSorenCount":1,"pieces":[{},{},{}],"turns":42}'
observation='{"schema":1,"game_id":"game-1","stop_id":null,"board":{"state":"MOVE","score":6111,"makeSorenCount":1,"pieces":[{},{},{}]},"observed_epoch":1791393000}'

write_with_age() {
	# $1=path $2=age秒 $3=内容
	python3 - "$1" "$2" "$3" <<'PY'
import os, sys, time
path, age, body = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as stream:
    stream.write(body)
stamp = time.time() - float(age)
os.utime(path, (stamp, stamp))
PY
}

write_pair() {
	# lifecycle request/ack を同一 identity で書く (実観測の障害時は cancel 後)。
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

write_cancelled_pair() {
	# cancel 済み (park ではない) ので、復旧判定は通常の監視経路に入る。
	write_pair cancelled "$(( $(date +%s) + 600 ))"
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

expect_no_destructive_calls() {
	if [ -s "$calls" ]; then
		echo "FAIL $1: destructive recovery calls: $(tr '\n' ' ' <"$calls")" >&2
		exit 1
	fi
}

expect_relaunch() {
	grep -qx 'relaunch' "$calls" || {
		echo "FAIL $1: expected relaunch, calls=$(tr '\n' ' ' <"$calls")" >&2
		exit 1
	}
}

# 1) cancel 後の再開。game_state.json は 806s 停滞、実観測は生存。
#    → 保持中の盤面と判定し、kill/relaunch/tmux/lease を 0 回で試合を継続する。
write_cancelled_pair
write_with_age "$test_root/game_state.json" 806 "$board"
write_with_age "$BRIDGE_OBSERVATION_FILE" 1 "$observation"
: >"$calls"; : >"$test_root/bridge.log"
expect_rc 0 "stale mtime with live observation" _ensure_bridge_alive
expect_no_destructive_calls "stale mtime with live observation"
grep -q "実観測は生存" "$test_root/bridge.log" || {
	echo "FAIL: hold が明示ログされていない" >&2
	exit 1
}
grep -q "score:6111" "$test_root/bridge.log" || {
	echo "FAIL: 保持対象の盤面がログに無い" >&2
	exit 1
}

# 2) 休止解除直後の 1 周回で実観測がまだ古い場合も、有界の再観測で生存を確認する
#    (観測は最長 1 秒ごとに更新される)。
write_with_age "$test_root/game_state.json" 806 "$board"
rm -f "$BRIDGE_OBSERVATION_FILE"
: >"$calls"
{
	sleep 0.3
	python3 - "$BRIDGE_OBSERVATION_FILE" "$observation" <<'PY'
import sys
open(sys.argv[1], "w", encoding="utf-8").write(sys.argv[2])
PY
} &
writer_pid=$!
expect_rc 0 "observation refreshed during re-observation" _ensure_bridge_alive
wait "$writer_pid" 2>/dev/null || true
expect_no_destructive_calls "observation refreshed during re-observation"

# 3) 実観測も途絶えている (本当に観測していない) 場合は従来どおり復旧する。
#    過剰な抑止で復旧できなくならないことを固定する。
write_with_age "$test_root/game_state.json" 806 "$board"
write_with_age "$BRIDGE_OBSERVATION_FILE" 900 "$observation"
: >"$calls"
expect_rc 0 "stale mtime without live observation" _ensure_bridge_alive
expect_relaunch "stale mtime without live observation"

# 4) プロセス消失は従来どおり復旧する (ガードは停滞の裏取りにだけ働く)。
_br_target_pids() { :; }
write_with_age "$test_root/game_state.json" 806 "$board"
write_with_age "$BRIDGE_OBSERVATION_FILE" 1 "$observation"
: >"$calls"
expect_rc 0 "process gone" _ensure_bridge_alive
expect_relaunch "process gone"
_br_target_pids() { echo 4242; }

# 5) 盤面が進み始めたら (game_state fresh) 保留ログも復旧も出さず通常運転へ戻る。
write_with_age "$test_root/game_state.json" 5 "$board"
write_with_age "$BRIDGE_OBSERVATION_FILE" 1 "$observation"
: >"$calls"; : >"$test_root/bridge.log"
expect_rc 0 "fresh game_state" _ensure_bridge_alive
expect_no_destructive_calls "fresh game_state"
if grep -q "実観測は生存" "$test_root/bridge.log"; then
	echo "FAIL: fresh な盤面で保留ログが出た" >&2
	exit 1
fi

echo "ok: bridge recovery keeps the board unless the live observation is gone"
