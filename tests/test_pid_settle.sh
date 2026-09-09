#!/bin/bash
# Regression: handover verifications must accept a gone-or-zombie worker.
# kill -0 succeeds on zombies, so checking liveness right after KILL fails on
# our own kill until the owning supervisor reaps it (2026-09-10 production
# quiesce failures: daemon verified "alive" microseconds after KILL).
# Fakes mirror the VM repro that hit 10/10: a 100%-busy supervisor stub that
# cannot reap promptly, and daemons that outlast the 2s TERM window.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0
pass() { echo "ok: $1"; }
fail() { echo "FAIL: $1"; failures=$((failures + 1)); }

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pid-settle.XXXXXX")"
cleanup() {
	kill "$(jobs -p 2>/dev/null || true)" 2>/dev/null || true
	wait 2>/dev/null || true
	rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

LOAD_HELPERS='
log() { :; }
source "'"$repo_root"'/infra/cleanup.sh"
'

# --- unit: _pid_gone_or_zombie ---
echo "--- helper unit ---"
bash -c "$LOAD_HELPERS
sleep 30 & live=\$!
_pid_gone_or_zombie \"\$live\" && echo LIVE-SEES-STOPPED || echo LIVE-SEES-RUNNING
kill -KILL \"\$live\" 2>/dev/null; wait \"\$live\" 2>/dev/null
_pid_gone_or_zombie \"\$live\" && echo REAPED-SEES-STOPPED || echo REAPED-SEES-RUNNING
_pid_gone_or_zombie '' && echo EMPTY-STOPPED || echo EMPTY-RUNNING
_pid_gone_or_zombie 'abc' && echo NONNUM-STOPPED || echo NONNUM-RUNNING
" > "$TEST_ROOT/unit.out" 2>&1
grep -q "LIVE-SEES-RUNNING" "$TEST_ROOT/unit.out" && pass "live is running" || fail "live misread"
grep -q "REAPED-SEES-STOPPED" "$TEST_ROOT/unit.out" && pass "reaped is stopped" || fail "reaped misread"
grep -q "EMPTY-STOPPED" "$TEST_ROOT/unit.out" && pass "empty is stopped" || fail "empty misread"
grep -q "NONNUM-STOPPED" "$TEST_ROOT/unit.out" && pass "non-numeric is stopped" || fail "non-numeric misread"

# --- unit: zombie (owned by a busy stub that cannot reap promptly) ---
printf '#!/bin/bash\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/victim.sh"
chmod +x "$TEST_ROOT/victim.sh"
( while true; do ps -Ao pid= >/dev/null 2>&1; done ) &
STUB_BUSY=$!
bash "$TEST_ROOT/victim.sh" & VICTIM=$!
sleep 0.5
kill -9 "$VICTIM" 2>/dev/null || true
bash -c "$LOAD_HELPERS
if _pid_gone_or_zombie \"$VICTIM\"; then echo ZOMBIE-SEES-STOPPED; else echo ZOMBIE-SEES-RUNNING; fi
" > "$TEST_ROOT/zombie.out" 2>&1
grep -q "ZOMBIE-SEES-STOPPED" "$TEST_ROOT/zombie.out" && pass "zombie is stopped" || fail "zombie misread as running"
kill -KILL "$STUB_BUSY" "$VICTIM" 2>/dev/null || true

# --- unit: _pid_stopped_settled bounds ---
bash -c "$LOAD_HELPERS
sleep 30 & live=\$!
T0=\$(date +%s)
_pid_stopped_settled \"\$live\" 3 && echo SETTLE-LIVE-STOPPED || echo SETTLE-LIVE-RUNNING
T1=\$(date +%s)
echo \"elapsed=\$((T1-T0))\"
kill -KILL \"\$live\" 2>/dev/null; wait \"\$live\" 2>/dev/null
_pid_stopped_settled \"\$live\" 30 && echo SETTLE-DEAD-STOPPED || echo SETTLE-DEAD-RUNNING
" > "$TEST_ROOT/settle.out" 2>&1
grep -q "SETTLE-LIVE-RUNNING" "$TEST_ROOT/settle.out" && pass "settled live fails" || fail "settled live wrong"
grep -q "SETTLE-DEAD-STOPPED" "$TEST_ROOT/settle.out" && pass "settled dead succeeds" || fail "settled dead wrong"

# --- pause-level: slow-trap daemon + busy supervisor stub ---
echo "--- pause with KILL-path daemon ---"
mkdir -p "$TEST_ROOT/root/tmp/state" "$TEST_ROOT/root/tmp/state/game_lifecycle"
printf '#!/bin/bash\ntrap "sleep 2.5; exit 0" TERM\nwhile true; do sleep 0.2; done\n' > "$TEST_ROOT/root/improve_daemon.sh"
chmod +x "$TEST_ROOT/root/improve_daemon.sh"
echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}' \
	> "$TEST_ROOT/root/tmp/state/improve_state.json"
cat > "$TEST_ROOT/stub.sh" <<'STUB'
#!/bin/bash
# Busy supervisor stub: owns the daemon, never reaps promptly.
cd "$1" || exit 1
exec bash ./improve_daemon.sh &
echo $! > "$2/pid"
while true; do ps -Ao pid= >/dev/null 2>&1; done
STUB
chmod +x "$TEST_ROOT/stub.sh"
bash "$TEST_ROOT/stub.sh" "$TEST_ROOT/root" "$TEST_ROOT" &
STUB_PID=$!
sleep 0.5
DPID=$(cat "$TEST_ROOT/pid")
echo "$DPID" > "$TEST_ROOT/root/tmp/state/improve_daemon.pid"
(cd "$TEST_ROOT/root" && \
GAME_LIFECYCLE_ROOT="$TEST_ROOT/root" TMP_STATE_DIR="tmp/state" \
IMPROVE_DAEMON_PID_FILE="$TEST_ROOT/root/tmp/state/improve_daemon.pid" \
IMPROVE_STATE_FILE="$TEST_ROOT/root/tmp/state/improve_state.json" \
IMPROVE_PID=0 GAME_LIFECYCLE_LOCK_TIMEOUT_SEC=10 \
bash -c '
	set -uo pipefail
	cd "'"$TEST_ROOT"'/root" || exit 2
	GAME_LIFECYCLE_ROOT="'"$TEST_ROOT"'/root"
	GAME_LIFECYCLE_DIR="'"$TEST_ROOT"'/root/tmp/state/game_lifecycle"
	GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
	log() { echo "[pid-settle] $*" >> "'"$TEST_ROOT"'/events.log"; }
	source "'"$repo_root"'/lib/game_lifecycle.sh"
	source "'"$repo_root"'/strategy/improve.sh"
	source "'"$repo_root"'/infra/cleanup.sh"
	_game_lifecycle_pause_improvements "44444444-2222-4333-8444-555555555555"
	echo $? > "'"$TEST_ROOT"'/rc"
')
RC=$(cat "$TEST_ROOT/rc" 2>/dev/null || echo MISSING)
[ "$RC" = "0" ] && pass "KILL-path pause rc=0" || fail "KILL-path pause rc=$RC (want 0)"
[ -f "$TEST_ROOT/root/tmp/state/improve_daemon.paused" ] && pass "marker held" || fail "marker missing"
[ -f "$TEST_ROOT/root/tmp/state/game_lifecycle/improvement_pause.json" ] && pass "record written" || fail "record missing"
grep -q "確認できません" "$TEST_ROOT/events.log" 2>/dev/null && fail "false verify failure logged" || pass "no false verify failure"
kill -KILL "$STUB_PID" "$DPID" 2>/dev/null || true

if [ "$failures" -gt 0 ]; then echo "RESULT: $failures failure(s)"; exit 1; fi
echo "RESULT: all pid-settle cases pass"
