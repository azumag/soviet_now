#!/bin/bash
# Regression: a boundary-triggered improve job spawning between the entry
# child check and the post-kill verification must be drained, not fail the
# pause. Production hit this on 2026-09-10 02:22-02:40 JST: four consecutive
# sorengame handover stops failed with improve_stop_failed while improve jobs
# were running across match boundaries. The daemon itself stopped fine; the
# late-child check ("改善子ジョブが停止後に再出現") rejected a job that the
# daemon had spawned after the entry check, even though draining it is
# exactly what the handover owns.
#
# Fakes: a TERM-trapping daemon (1.5s graceful cleanup like the real one) and
# eloop_improve.sh children driven through the real IMPROVE_STATE_FILE path,
# so _find_live_improve_pid / _stop_improve_pid_if_running run unmodified.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0



cleanup_root() {
	local pids
	pids="$(jobs -p 2>/dev/null || true)"
	[ -n "$pids" ] && kill $pids 2>/dev/null || true
	wait 2>/dev/null || true
	[ -n "${TEST_ROOT:-}" ] && rm -rf "$TEST_ROOT"
}
trap cleanup_root EXIT

# NOTE: each case runs the pause in a fresh bash with the repo functions
# sourced. Fake processes are started by the outer shell so the pause under
# test never parents them, mirroring production where the lifecycle
# controller is not the daemon/job parent.


pass() { echo "ok: $1"; }
fail() { echo "FAIL: $1"; failures=$((failures + 1)); }

# --- case 1: late spawn between entry check and post-kill verification ---
echo "--- late_spawn_is_drained ---"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pause-race.XXXXXX")"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
printf '#!/bin/bash\ntrap "sleep 1.5; exit 0" TERM\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/improve_daemon.sh"
chmod +x "$TEST_ROOT/root/improve_daemon.sh"
printf '#!/bin/bash\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/eloop_improve.sh"
chmod +x "$TEST_ROOT/root/eloop_improve.sh"
echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}' \
	> "$TEST_ROOT/root/tmp/state/improve_state.json"
(cd "$TEST_ROOT/root" && exec bash ./improve_daemon.sh & echo $! > "$TEST_ROOT/daemon.pid")
DPID=$(cat "$TEST_ROOT/daemon.pid")
echo "$DPID" > "$TEST_ROOT/root/tmp/state/improve_daemon.pid"
export TEST_ROOT
# Pause in background; spawner waits for the marker (created before the entry
# check) then spawns the boundary job mid-pause.
GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" TMP_STATE_DIR="tmp/state" \
IMPROVE_DAEMON_PID_FILE="$TEST_ROOT/root/tmp/state/improve_daemon.pid" \
IMPROVE_STATE_FILE="$TEST_ROOT/root/tmp/state/improve_state.json" \
IMPROVE_PID=0 GAME_LIFECYCLE_LOCK_TIMEOUT_SEC=10 \
bash -c '
	set -uo pipefail
	cd "'"$TEST_ROOT"'/root" || exit 2
	# game_lifecycle.sh pins GAME_LIFECYCLE_ROOT/DIR at source time; re-point
	# them at this case root like tests/test_game_lifecycle.sh does.
	GAME_LIFECYCLE_ROOT="$TEST_ROOT/root"
	GAME_LIFECYCLE_DIR="$TEST_ROOT/root/tmp/state/game_lifecycle"
	GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
	log() { echo "[pause-race] $*" >> "'"$TEST_ROOT"'/events.log"; }
	source "'"$repo_root"'/lib/game_lifecycle.sh"
	source "'"$repo_root"'/strategy/improve.sh"
	source "'"$repo_root"'/infra/cleanup.sh"
	_game_lifecycle_pause_improvements "11111111-2222-4333-8444-555555555555"
	echo $? > "'"$TEST_ROOT"'/rc"
' &
PAUSE_PID=$!
( while [ ! -f "$TEST_ROOT/root/tmp/state/improve_daemon.paused" ]; do sleep 0.05; done
  sleep 0.3
  (cd "$TEST_ROOT/root" && exec bash ./eloop_improve.sh & echo $! > "$TEST_ROOT/child.pid")
  CPID=$(cat "$TEST_ROOT/child.pid")
  python3 - "$TEST_ROOT/root/tmp/state/improve_state.json" "$CPID" <<'PY'
import json, sys
json.dump({"status": "running", "pid": int(sys.argv[2]), "strategy_hash_before": "",
           "phase": "running", "progress": 50, "detail": "race",
           "started_at": 0, "updated_at": 0}, open(sys.argv[1], "w"))
PY
  echo "spawned child=$CPID" >> "$TEST_ROOT/events.log"
) &
wait "$PAUSE_PID" 2>/dev/null || true
RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
CPID=$(cat "$TEST_ROOT/child.pid" 2>/dev/null || echo NONE)
if [ "$RC" = "0" ]; then pass "late_spawn rc=0"; else fail "late_spawn rc=$RC (want 0)"; fi
if [ "$CPID" != "NONE" ] && kill -0 "$CPID" 2>/dev/null; then fail "late child still alive ($CPID)"; else pass "late child stopped"; fi
if kill -0 "$DPID" 2>/dev/null; then fail "daemon still alive"; else pass "daemon stopped"; fi
[ -f "$TEST_ROOT/root/tmp/state/improve_daemon.paused" ] && pass "marker held" || fail "marker missing"
[ -f "$TEST_ROOT/root/tmp/state/game_lifecycle/improvement_pause.json" ] && pass "record written" || fail "record missing"
grep -q "再出現" "$TEST_ROOT/events.log" 2>/dev/null && echo "note: late-child path was exercised"
kill "$CPID" "$DPID" 2>/dev/null || true
rm -rf "$TEST_ROOT"; unset TEST_ROOT

# --- case 2: second wave during the late drain is also absorbed ---
echo "--- second_wave_is_absorbed ---"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pause-race.XXXXXX")"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
printf '#!/bin/bash\ntrap "sleep 1.5; exit 0" TERM\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/improve_daemon.sh"
chmod +x "$TEST_ROOT/root/improve_daemon.sh"
printf '#!/bin/bash\ntrap "sleep 1; exit 0" TERM\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/eloop_improve.sh"
chmod +x "$TEST_ROOT/root/eloop_improve.sh"
echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}' \
	> "$TEST_ROOT/root/tmp/state/improve_state.json"
(cd "$TEST_ROOT/root" && exec bash ./improve_daemon.sh & echo $! > "$TEST_ROOT/daemon.pid")
DPID=$(cat "$TEST_ROOT/daemon.pid")
echo "$DPID" > "$TEST_ROOT/root/tmp/state/improve_daemon.pid"
export TEST_ROOT
GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" TMP_STATE_DIR="tmp/state" \
IMPROVE_DAEMON_PID_FILE="$TEST_ROOT/root/tmp/state/improve_daemon.pid" \
IMPROVE_STATE_FILE="$TEST_ROOT/root/tmp/state/improve_state.json" \
IMPROVE_PID=0 GAME_LIFECYCLE_LOCK_TIMEOUT_SEC=10 \
bash -c '
	set -uo pipefail
	cd "'"$TEST_ROOT"'/root" || exit 2
	# game_lifecycle.sh pins GAME_LIFECYCLE_ROOT/DIR at source time; re-point
	# them at this case root like tests/test_game_lifecycle.sh does.
	GAME_LIFECYCLE_ROOT="$TEST_ROOT/root"
	GAME_LIFECYCLE_DIR="$TEST_ROOT/root/tmp/state/game_lifecycle"
	GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
	log() { echo "[pause-race] $*" >> "'"$TEST_ROOT"'/events.log"; }
	source "'"$repo_root"'/lib/game_lifecycle.sh"
	source "'"$repo_root"'/strategy/improve.sh"
	source "'"$repo_root"'/infra/cleanup.sh"
	_game_lifecycle_pause_improvements "22222222-2222-4333-8444-555555555555"
	echo $? > "'"$TEST_ROOT"'/rc"
' &
PAUSE_PID=$!
( while [ ! -f "$TEST_ROOT/root/tmp/state/improve_daemon.paused" ]; do sleep 0.05; done
  sleep 0.3
  (cd "$TEST_ROOT/root" && exec bash ./eloop_improve.sh & echo $! > "$TEST_ROOT/child1.pid")
  C1=$(cat "$TEST_ROOT/child1.pid")
  python3 - "$TEST_ROOT/root/tmp/state/improve_state.json" "$C1" <<'PY'
import json, sys
json.dump({"status": "running", "pid": int(sys.argv[2]), "strategy_hash_before": "",
           "phase": "running", "progress": 50, "detail": "race",
           "started_at": 0, "updated_at": 0}, open(sys.argv[1], "w"))
PY
  while ! grep -q "late_child" "$TEST_ROOT/events.log" 2>/dev/null; do sleep 0.05; done
  (cd "$TEST_ROOT/root" && exec bash ./eloop_improve.sh & echo $! > "$TEST_ROOT/child2.pid")
  C2=$(cat "$TEST_ROOT/child2.pid")
  python3 - "$TEST_ROOT/root/tmp/state/improve_state.json" "$C2" <<'PY'
import json, sys
json.dump({"status": "running", "pid": int(sys.argv[2]), "strategy_hash_before": "",
           "phase": "running", "progress": 50, "detail": "race",
           "started_at": 0, "updated_at": 0}, open(sys.argv[1], "w"))
PY
  echo "spawned wave2=$C2" >> "$TEST_ROOT/events.log"
) &
wait "$PAUSE_PID" 2>/dev/null || true
RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
[ "$RC" = "0" ] && pass "second_wave rc=0" || fail "second_wave rc=$RC (want 0)"
[ -f "$TEST_ROOT/child2.pid" ] && pass "wave2 actually spawned" || fail "wave2 never spawned (vacuous test)"
for c in child1 child2; do
	C=$(cat "$TEST_ROOT/$c.pid" 2>/dev/null || echo NONE)
	if [ "$C" != "NONE" ] && kill -0 "$C" 2>/dev/null; then fail "$c still alive ($C)"; fi
done
pass "both waves stopped (or reported above)"
kill "$(cat "$TEST_ROOT/child1.pid" 2>/dev/null || echo "")" "$(cat "$TEST_ROOT/child2.pid" 2>/dev/null || echo "")" "$DPID" 2>/dev/null || true
rm -rf "$TEST_ROOT"; unset TEST_ROOT

# --- case 3: fail-closed when the drain helper is unavailable ---
echo "--- fail_closed_without_helper ---"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pause-race.XXXXXX")"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
printf '#!/bin/bash\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/improve_daemon.sh"
chmod +x "$TEST_ROOT/root/improve_daemon.sh"
printf '#!/bin/bash\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/eloop_improve.sh"
chmod +x "$TEST_ROOT/root/eloop_improve.sh"
echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}' \
	> "$TEST_ROOT/root/tmp/state/improve_state.json"
(cd "$TEST_ROOT/root" && exec bash ./improve_daemon.sh & echo $! > "$TEST_ROOT/daemon.pid")
DPID=$(cat "$TEST_ROOT/daemon.pid")
echo "$DPID" > "$TEST_ROOT/root/tmp/state/improve_daemon.pid"
(cd "$TEST_ROOT/root" && exec bash ./eloop_improve.sh & echo $! > "$TEST_ROOT/child.pid")
CPID=$(cat "$TEST_ROOT/child.pid")
python3 - "$TEST_ROOT/root/tmp/state/improve_state.json" "$CPID" <<'PY'
import json, sys
json.dump({"status": "running", "pid": int(sys.argv[2]), "strategy_hash_before": "",
           "phase": "running", "progress": 50, "detail": "race",
           "started_at": 0, "updated_at": 0}, open(sys.argv[1], "w"))
PY
export TEST_ROOT
(cd "$TEST_ROOT/root" && GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" TMP_STATE_DIR="tmp/state" \
IMPROVE_DAEMON_PID_FILE="$TEST_ROOT/root/tmp/state/improve_daemon.pid" \
IMPROVE_STATE_FILE="$TEST_ROOT/root/tmp/state/improve_state.json" \
IMPROVE_PID=0 GAME_LIFECYCLE_LOCK_TIMEOUT_SEC=10 \
bash -c '
	set -uo pipefail
	cd "'"$TEST_ROOT"'/root" || exit 2
	# game_lifecycle.sh pins GAME_LIFECYCLE_ROOT/DIR at source time; re-point
	# them at this case root like tests/test_game_lifecycle.sh does.
	GAME_LIFECYCLE_ROOT="$TEST_ROOT/root"
	GAME_LIFECYCLE_DIR="$TEST_ROOT/root/tmp/state/game_lifecycle"
	GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
	log() { echo "[pause-race] $*" >> "'"$TEST_ROOT"'/events.log"; }
	source "'"$repo_root"'/lib/game_lifecycle.sh"
	source "'"$repo_root"'/strategy/improve.sh"
	source "'"$repo_root"'/infra/cleanup.sh"
	unset -f _stop_improve_pid_if_running
	_game_lifecycle_pause_improvements "33333333-2222-4333-8444-555555555555"
	echo $? > "'"$TEST_ROOT"'/rc"
')
RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
[ "$RC" != "0" ] && pass "no-helper fails closed rc=$RC" || fail "no-helper rc=0 (must fail closed)"
if kill -0 "$CPID" 2>/dev/null; then pass "child untouched without helper"; else fail "child killed without helper"; fi
kill "$CPID" "$DPID" 2>/dev/null || true
rm -rf "$TEST_ROOT"; unset TEST_ROOT

# --- case 4: prediction pause accepts the zombie its own marker produced ---
# Production 2026-09-25 09:53 JST: _game_lifecycle_pause_predictions writes
# tmp/state/prediction_worker.paused first, and prediction_worker.sh exits on
# seeing it ("pause file detected → exit").  The recorded PID was therefore a
# zombie by the time the stop was verified: kill -0 still succeeded, the old
# check called that a failed stop, and sorengame→nethack aborted with
# prediction_stop_failed, parking the rotation in recovery_required for hours.
echo "--- prediction_pause_accepts_own_zombie ---"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pause-race.XXXXXX")"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
ZPID_FILE="$TEST_ROOT/zombie.pid"
python3 - "$ZPID_FILE" <<'PY' &
import os
import sys
import time

child = os.fork()
if child == 0:
    os._exit(0)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(str(child))
# Deliberately do not waitpid yet: the child stays Z while this parent lives.
time.sleep(30)
os.waitpid(child, 0)
PY
ZPARENT=$!
for _ in $(seq 1 50); do
	[ -s "$ZPID_FILE" ] && break
	sleep 0.1
done
ZPID=$(cat "$ZPID_FILE" 2>/dev/null || echo "")
# ps, not /proc: the precondition must hold on macOS too, where this suite is
# also run locally.  Z* covers Linux suffixes like Zs/Z+.
ZSTATE=$(ps -p "$ZPID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)
case "$ZSTATE" in
Z*) zombied=1 ;;
*) zombied=0 ;;
esac
if [ "$zombied" -ne 1 ]; then
	fail "real zombie precondition not reached (pid=${ZPID:-none}, state=${ZSTATE:-none})"
	kill "$ZPARENT" 2>/dev/null || true
	wait "$ZPARENT" 2>/dev/null || true
	rm -rf "$TEST_ROOT"; unset TEST_ROOT
else
	echo "$ZPID" > "$TEST_ROOT/root/tmp/state/prediction_worker.pid"
	export TEST_ROOT
	GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" ELOOP_LIB_DIR="$TEST_ROOT/root" \
	TMP_STATE_DIR="tmp/state" GAME_LIFECYCLE_DIR="$TEST_ROOT/root/tmp/state/game_lifecycle" \
	bash -c '
		set -uo pipefail
		cd "'"$TEST_ROOT"'/root" || exit 2
		# ELOOP_LIB_DIR pins the root: game_lifecycle.sh recomputes
		# GAME_LIFECYCLE_ROOT from it, otherwise the marker and record land in
		# the checkout instead of this case root.
		ELOOP_LIB_DIR="'"$TEST_ROOT"'/root"
		GAME_LIFECYCLE_ROOT="'"$TEST_ROOT"'/root"
		TMP_STATE_DIR="tmp/state"
		GAME_LIFECYCLE_DIR="'"$TEST_ROOT"'/root/tmp/state/game_lifecycle"
		log() { echo "[pause-race] $*" >> "'"$TEST_ROOT"'/events.log"; }
		source "'"$repo_root"'/lib/game_lifecycle.sh"
		source "'"$repo_root"'/infra/cleanup.sh"
		# Precondition: the combination that used to fail the whole switch.
		if _game_lifecycle_is_prediction_worker_pid "'"$ZPID"'"; then
			echo IDENTITY-MATCH >> "'"$TEST_ROOT"'/precond"
		else
			echo IDENTITY-MISMATCH >> "'"$TEST_ROOT"'/precond"
		fi
		if kill -0 "'"$ZPID"'" 2>/dev/null; then echo ALIVE-KILL0 >> "'"$TEST_ROOT"'/precond"; fi
		_game_lifecycle_pause_predictions "55555555-2222-4333-8444-555555555555"
		echo $? > "'"$TEST_ROOT"'/rc"
	'
	grep -q "IDENTITY-MISMATCH" "$TEST_ROOT/precond" 2>/dev/null && pass "zombie is not recognised as a live worker" || fail "precondition vacuous: identity matched"
	grep -q "ALIVE-KILL0" "$TEST_ROOT/precond" 2>/dev/null && pass "kill -0 still succeeds on it (old failure condition)" || fail "precondition vacuous: kill -0 already failed"
	RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
	[ "$RC" = "0" ] && pass "own-zombie pause rc=0" || fail "own-zombie pause rc=$RC (want 0)"
	[ -f "$TEST_ROOT/root/tmp/state/prediction_worker.paused" ] && pass "marker held" || fail "marker missing"
	[ -f "$TEST_ROOT/root/tmp/state/game_lifecycle/prediction_pause.json" ] && pass "record written" || fail "record missing"
	grep -q "予想ワーカーの停止確認に失敗" "$TEST_ROOT/events.log" 2>/dev/null && fail "false prediction failure logged" || pass "no false prediction failure"
	kill "$ZPARENT" 2>/dev/null || true
	wait "$ZPARENT" 2>/dev/null || true
	rm -rf "$TEST_ROOT"; unset TEST_ROOT
fi

# --- case 5: a live PID owned by an unrelated command still fails closed ---
echo "--- prediction_pause_refuses_foreign_live_pid ---"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pause-race.XXXXXX")"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
sleep 30 & FOREIGN=$!
echo "$FOREIGN" > "$TEST_ROOT/root/tmp/state/prediction_worker.pid"
export TEST_ROOT
GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" ELOOP_LIB_DIR="$TEST_ROOT/root" \
TMP_STATE_DIR="tmp/state" GAME_LIFECYCLE_DIR="$TEST_ROOT/root/tmp/state/game_lifecycle" \
bash -c '
	set -uo pipefail
	cd "'"$TEST_ROOT"'/root" || exit 2
	ELOOP_LIB_DIR="'"$TEST_ROOT"'/root"
	GAME_LIFECYCLE_ROOT="'"$TEST_ROOT"'/root"
	TMP_STATE_DIR="tmp/state"
	GAME_LIFECYCLE_DIR="'"$TEST_ROOT"'/root/tmp/state/game_lifecycle"
	log() { echo "[pause-race] $*" >> "'"$TEST_ROOT"'/events.log"; }
	source "'"$repo_root"'/lib/game_lifecycle.sh"
	source "'"$repo_root"'/infra/cleanup.sh"
	_game_lifecycle_pause_predictions "66666666-2222-4333-8444-555555555555"
	echo $? > "'"$TEST_ROOT"'/rc"
'
RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
[ "$RC" != "0" ] && pass "foreign live pid fails closed rc=$RC" || fail "foreign live pid rc=0 (must refuse)"
if kill -0 "$FOREIGN" 2>/dev/null; then pass "foreign pid untouched"; else fail "foreign pid was killed"; fi
[ ! -f "$TEST_ROOT/root/tmp/state/prediction_worker.paused" ] && pass "marker released on refusal" || fail "marker leaked on refusal"
kill "$FOREIGN" 2>/dev/null || true
wait "$FOREIGN" 2>/dev/null || true
rm -rf "$TEST_ROOT"; unset TEST_ROOT

if [ "$failures" -gt 0 ]; then echo "RESULT: $failures failure(s)"; exit 1; fi
echo "RESULT: all pause-race cases pass"
