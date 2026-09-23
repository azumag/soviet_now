#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/game-lifecycle-shell.XXXXXX")"
cleanup() {
	if [ -n "${watchdog_pid:-}" ]; then
		kill -KILL "$watchdog_pid" 2>/dev/null || true
		wait "$watchdog_pid" 2>/dev/null || true
	fi
	if [ -n "${watchdog_launcher_pid:-}" ]; then
		kill -KILL "$watchdog_launcher_pid" 2>/dev/null || true
		wait "$watchdog_launcher_pid" 2>/dev/null || true
	fi
	if [ -n "${unrelated_pid:-}" ]; then
		kill "$unrelated_pid" 2>/dev/null || true
	fi
	rm -rf "$test_root"
}
trap cleanup EXIT

source "$repo_root/lib/game_lifecycle.sh"
TMP_STATE_DIR="$test_root/state"
GAME_LIFECYCLE_ROOT="$test_root"
GAME_LIFECYCLE_DIR="$test_root/lifecycle"
GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
GAME_LIFECYCLE_PREDICTION_PAUSE_FILE="$GAME_LIFECYCLE_DIR/prediction_pause.json"
GAME_LIFECYCLE_PREDICTION_MARKER="$TMP_STATE_DIR/prediction_worker.paused"
GAME_LIFECYCLE_WATCHDOG_PAUSE_FILE="$GAME_LIFECYCLE_DIR/watchdog_pause.json"
GAME_LIFECYCLE_WATCHDOG_MARKER="$TMP_STATE_DIR/soviet_watchdog.paused"
GAME_LIFECYCLE_LOOP_PAUSE_FILE="$TMP_STATE_DIR/soren_loop.paused"
GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE="$GAME_LIFECYCLE_DIR/loop_pause.json"
IMPROVE_DAEMON_PID_FILE="$test_root/improve_daemon.pid"
mkdir -p "$TMP_STATE_DIR" "$GAME_LIFECYCLE_DIR"

request_id="08842091-bf83-4490-9102-40af8ecc98cc"
events="$test_root/events"
resource_status_file="$test_root/resource_status"
: >"$resource_status_file"
: >"$events"

# Real lifecycle records with shared identity (game/generation/deadline).
# The broker paths under test now read request.json + ack.json in a single
# invocation, so the harness drives real files instead of mocked accessors.
write_lifecycle_pair() {
	local status="$1" deadline_epoch="${2:-}"
	python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" "$request_id" "$status" "$deadline_epoch" <<'PY'
import json
import sys
import time

request_path, ack_path, rid, status = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
deadline = float(sys.argv[5]) if sys.argv[5] else time.time() + 600.0
request = {
    "schema": 1,
    "request_id": rid,
    "game": "sorengame",
    "generation": 1,
    "deadline_epoch": deadline,
    "deadline_at": "2030-01-01T00:00:00.000Z",
}
ack = dict(request, status=status)
for target, value in ((request_path, request), (ack_path, ack)):
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
PY
}

set_ack() {
	python3 - "$GAME_LIFECYCLE_DIR/ack.json" "$1" <<'PY'
import json
import sys

path, status = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as stream:
    value = json.load(stream)
value["status"] = status
with open(path, "w", encoding="utf-8") as stream:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
PY
}

_find_live_improve_pid() { return 1; }
log() { :; }
game_lifecycle_resource_status() { cat "$resource_status_file"; }
_game_lifecycle_cli() {
	local command="${1:-}"
	printf '%s\n' "$command" >>"$events"
	case "$command" in
	boundary)
		if [ "${mock_boundary_waiting:-0}" -eq 1 ]; then
			set_ack waiting
			return 1
		fi
		set_ack boundary
		return 0
		;;
	stop) set_ack stop_requested; return 0 ;;
	finish) set_ack stopped; return 0 ;;
	cancel) set_ack cancelled; return 0 ;;
	*) return 0 ;;
	esac
}
_game_lifecycle_wait_resource() { printf '%s\n' stopped >"$resource_status_file"; return 0; }

# A runner exit is not itself a game boundary. A founding animation can leave
# the same board alive, and waiting for its actual end must keep the player
# running. Neither the normal preparation nor startup may send retry/reset.
source "$repo_root/eloop.sh"
STRATEGY_FILE="$test_root/strategy.py"
printf 'same-strategy\n' >"${STRATEGY_FILE}.game_snapshot"
printf '{"state":"STOP","score":6111,"pieces":[{"type":16}]}\n' >"$test_root/game_state.json"
cp "$test_root/game_state.json" "$test_root/original_game_state.json"
is_game_over() { printf 'is_game_over\n' >>"$events"; return 0; }
wait_for_move() { printf 'wait_for_move\n' >>"$events"; return 1; }
send_retry() { printf 'retry\n' >>"$events"; return 0; }
mock_boundary_waiting=1
for live_status in accepted waiting; do
	write_lifecycle_pair "$live_status"
	: >"$events"
	live_rc=0
	game_lifecycle_after_game || live_rc=$?
	[ "$live_rc" -eq 4 ]
	[ "$(game_lifecycle_ack_status)" = "waiting" ]
	[ ! -e "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]
	[ ! -e "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" ]
	[ ! -e "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]
	prepare_next_game
	[ "$(wc -l <"$events" | tr -d ' ')" -eq 2 ]
	[ "$(grep -cx boundary "$events")" -eq 2 ]
	[ "$(cat "${STRATEGY_FILE}.game_snapshot")" = same-strategy ]
	cmp "$test_root/game_state.json" "$test_root/original_game_state.json"
done

# Run the real startup recovery/wait block without starting the supervisor or
# any worker. A lingering transient STOP must go straight to the same-board
# runner, even if the normal MOVE wait would have timed out.
python3 - "$repo_root/soren_loop.sh" "$test_root/startup.sh" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
start = source.index("# Controller crash recovery must run")
end = source.index('_abort_if_interrupted "$wait_rc"', start)
Path(sys.argv[2]).write_text(source[start:end])
PY
: >"$events"
(
	source "$test_root/startup.sh"
	[ "$wait_rc" -eq 0 ]
)
[ "$(cat "$events")" = boundary ]
[ ! -e "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]
cmp "$test_root/game_state.json" "$test_root/original_game_state.json"

# Waiting also leaves an operator's existing pause exactly as it was.
printf 'operator\n' >"$GAME_LIFECYCLE_LOOP_PAUSE_FILE"
live_rc=0
game_lifecycle_after_game || live_rc=$?
[ "$live_rc" -eq 4 ]
[ "$(cat "$GAME_LIFECYCLE_LOOP_PAUSE_FILE")" = operator ]
[ ! -e "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" ]
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE"
unset mock_boundary_waiting

# The normal path, with no lifecycle request, must also preserve a transient
# STOP (or a MOVE timeout) instead of treating the timeout as permission to
# reset. An actual GAMEOVER remains eligible for retry; interruption propagates.
(
	source "$repo_root/core/game_state.sh"
	GAME_STATE="$test_root/game_state.json"
	GAME_LIFECYCLE_ENABLED=0
	wait_for_move() { printf 'wait_for_move\n' >>"$events"; return "${mock_move_rc:-1}"; }
	send_retry() { printf 'retry\n' >>"$events"; return 0; }
	for live_state in STOP MOVE; do
		printf '{"state":"%s","score":6111,"pieces":[{"type":16}]}\n' "$live_state" >"$GAME_STATE"
		! is_game_over
		: >"$events"
		prepare_next_game
		[ "$(cat "$events")" = wait_for_move ]
	done
	printf '{"state":"GAMEOVER"}\n' >"$GAME_STATE"
	is_game_over
	: >"$events"
	prepare_next_game
	[ "$(cat "$events")" = retry ]
	printf '{"state":"STOP"}\n' >"$GAME_STATE"
	mock_move_rc=130
	: >"$events"
	interrupt_rc=0
	prepare_next_game || interrupt_rc=$?
	[ "$interrupt_rc" -eq 130 ]
	[ "$(cat "$events")" = wait_for_move ]
)
: >"$events"
write_lifecycle_pair accepted

# A successful boundary waits for the resource stop and then pauses only the
# game loop.  Resource stop is a separate, explicit operation.
set +e
game_lifecycle_after_game
boundary_rc=$?
set -e
[ "$boundary_rc" -eq 3 ]
[ -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]
grep -qx boundary "$events"
[ "$(wc -l <"$events" | tr -d ' ')" -eq 1 ]
[ "$(game_lifecycle_ack_status)" = "boundary" ]
game_lifecycle_stop_after_boundary
grep -qx stop "$events"
grep -qx finish "$events"
[ -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]
game_lifecycle_restore_improvements
[ ! -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]
[ ! -f "$TMP_STATE_DIR/improve_daemon.paused" ]

# Prediction ownership follows the same rule: lifecycle-created markers are
# removed on restore, while an operator's pre-existing pause is preserved.
_game_lifecycle_pause_predictions "$request_id"
[ -f "$TMP_STATE_DIR/prediction_worker.paused" ]
[ -f "$GAME_LIFECYCLE_PREDICTION_PAUSE_FILE" ]
game_lifecycle_restore_predictions
[ ! -f "$TMP_STATE_DIR/prediction_worker.paused" ]
[ ! -f "$GAME_LIFECYCLE_PREDICTION_PAUSE_FILE" ]
touch "$TMP_STATE_DIR/prediction_worker.paused"
_game_lifecycle_pause_predictions "$request_id"
game_lifecycle_restore_predictions
[ -f "$TMP_STATE_DIR/prediction_worker.paused" ]
rm -f "$TMP_STATE_DIR/prediction_worker.paused"

# An operator replacement after lifecycle pause must not be deleted.
_game_lifecycle_pause_predictions "$request_id"
printf 'operator\n' >"$TMP_STATE_DIR/prediction_worker.paused"
game_lifecycle_restore_predictions
[ "$(cat "$TMP_STATE_DIR/prediction_worker.paused")" = "operator" ]
rm -f "$TMP_STATE_DIR/prediction_worker.paused"

# Regression: a repeat pause of the SAME request keeps ownership of the marker
# it created, so the later restore removes it instead of leaking the pause
# gate.  Rewriting the record with marker_created=false made
# game_lifecycle_restore_improvements treat a lifecycle-created pause as
# operator-owned, leaving improve_daemon.paused behind forever.
_game_lifecycle_pause_improvements "$request_id"
[ -f "$TMP_STATE_DIR/improve_daemon.paused" ]
_game_lifecycle_pause_improvements "$request_id"
game_lifecycle_restore_improvements
[ ! -f "$TMP_STATE_DIR/improve_daemon.paused" ]
[ ! -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]

# Regression: the lifecycle lock must not permanently redirect the caller's
# stderr.  exec redirections persist, so a diagnostic written after the first
# acquire still has to reach the real stderr.
stderr_probe_file="$test_root/stderr_probe"
(
	# No call-level fd-2 redirect here: a redirect ON the function call would
	# restore fd 2 on return and mask the exec's persistent redirection.
	_game_lifecycle_pause_improvements "$request_id"
	echo lifecycle_stderr_probe >&2
) 2>"$stderr_probe_file"
grep -qx lifecycle_stderr_probe "$stderr_probe_file"
game_lifecycle_restore_improvements
[ ! -f "$TMP_STATE_DIR/improve_daemon.paused" ]
[ ! -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]

# An operator-created pause marker is preserved through rollback.
touch "$TMP_STATE_DIR/improve_daemon.paused"
_game_lifecycle_pause_improvements "$request_id"
game_lifecycle_restore_improvements
[ -f "$TMP_STATE_DIR/improve_daemon.paused" ]
rm -f "$TMP_STATE_DIR/improve_daemon.paused"

# Unsupported legacy mode cancels the request and restores a marker created by
# this handover; it must not pause or terminate the old game.
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" "$events" "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE"
: >"$events"
write_lifecycle_pair boundary
printf '%s\n' unsupported >"$resource_status_file"
_game_lifecycle_wait_resource() { printf '%s\n' unsupported >"$resource_status_file"; return 2; }
set +e
game_lifecycle_after_game
unsupported_rc=$?
set -e
[ "$unsupported_rc" -eq 3 ]
set +e
game_lifecycle_stop_after_boundary
unsupported_rc=$?
set -e
[ "$unsupported_rc" -eq 1 ]
grep -qx cancel "$events"
[ ! -f "$TMP_STATE_DIR/improve_daemon.paused" ]
[ ! -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]

# A real stop timeout fails closed: the control remains durable and the loop is
# paused rather than starting a fresh game whose resources are still live.
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE"
: >"$events"
write_lifecycle_pair boundary
: >"$resource_status_file"
_game_lifecycle_wait_resource() { return 1; }
set +e
game_lifecycle_after_game
timeout_rc=$?
set -e
[ "$timeout_rc" -eq 3 ]
set +e
game_lifecycle_stop_after_boundary
timeout_rc=$?
set -e
[ "$timeout_rc" -eq 2 ]
[ -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]

# A PID-file collision with an unrelated process is detected and left alive.
sleep 30 & unrelated_pid=$!
printf '%s\n' "$unrelated_pid" >"$IMPROVE_DAEMON_PID_FILE"
if _game_lifecycle_pause_improvements "$request_id"; then
	echo "unrelated daemon PID was accepted" >&2
	exit 1
fi
kill -0 "$unrelated_pid"
kill "$unrelated_pid"
wait "$unrelated_pid" 2>/dev/null || true
unset unrelated_pid
rm -f "$IMPROVE_DAEMON_PID_FILE"

# The broker.lock must be released on the failure path above: a retry with the
# collision cleared succeeds instead of deadlocking on a stale lock.
_game_lifecycle_pause_improvements "$request_id"
[ -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]
game_lifecycle_restore_improvements
[ ! -f "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" ]
[ ! -f "$TMP_STATE_DIR/improve_daemon.paused" ]
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE"

# bridge_parked: stopping parks like the other handover states; non-terminal
# parks expire with the request deadline while terminal stopped never expires.
now=$(date +%s)
future=$((now + 600))
past=$((now - 60))
expect_parked() {
	local want="$1" status="$2" deadline="$3" label="$4"
	write_lifecycle_pair "$status" "$deadline"
	set +e
	game_lifecycle_bridge_parked
	parked_rc=$?
	set -e
	if [ "$want" -eq 0 ] && [ "$parked_rc" -ne 0 ]; then
		echo "expected parked for $label (rc=$parked_rc)" >&2
		exit 1
	fi
	if [ "$want" -ne 0 ] && [ "$parked_rc" -eq 0 ]; then
		echo "expected NOT parked for $label" >&2
		exit 1
	fi
}
expect_parked 0 stopping "$future" "stopping/live"
expect_parked 1 stopping "$past" "stopping/expired"
expect_parked 0 stop_requested "$future" "stop_requested/live"
expect_parked 1 stop_requested "$past" "stop_requested/expired"
expect_parked 0 resume_requested "$future" "resume_requested/live"
expect_parked 1 resume_requested "$past" "resume_requested/expired"
expect_parked 0 stopped "$past" "stopped/expired-stays-parked"
expect_parked 1 boundary "$future" "boundary/never-parks"

# A generation-mismatched ack must not park either.
write_lifecycle_pair stopping "$future"
python3 - "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    value = json.load(stream)
value["game"] = "robots"
with open(path, "w", encoding="utf-8") as stream:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
PY
set +e
game_lifecycle_bridge_parked
parked_rc=$?
set -e
[ "$parked_rc" -ne 0 ]

# Split-read mixing is gone: a request paired with another generation's ack
# returns without parking the loop and without issuing any broker command.
python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" "$request_id" <<'PY'
import json
import sys
import time
import uuid

request_path, ack_path, rid = sys.argv[1], sys.argv[2], sys.argv[3]
deadline = time.time() + 600.0
request = {
    "schema": 1,
    "request_id": rid,
    "game": "sorengame",
    "generation": 1,
    "deadline_epoch": deadline,
    "deadline_at": "2030-01-01T00:00:00.000Z",
}
ack = dict(request, status="boundary", request_id=str(uuid.uuid4()))
for target, value in ((request_path, request), (ack_path, ack)):
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
PY
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE"
: >"$events"
set +e
game_lifecycle_after_game
mixed_rc=$?
set -e
[ "$mixed_rc" -eq 1 ]
[ ! -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]
[ ! -s "$events" ]
set +e
game_lifecycle_stop_after_boundary
mixed_rc=$?
set -e
[ "$mixed_rc" -eq 1 ]
[ ! -s "$events" ]

# A durable stopping claim keeps the loop parked (never starts a next game)
# and the startup crash-recovery path completes the exact claim: with the
# bridge's stopped resource already written, resume_pending finishes the
# request and returns 0 so the loop exits cleanly instead of retrying.
write_lifecycle_pair stopping
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE"
: >"$events"
# The bridge already wrote its stopped resource in this scenario, so the
# resource wait succeeds immediately (last definition above returns 1).
_game_lifecycle_wait_resource() { printf '%s\n' stopped >"$resource_status_file"; return 0; }
set +e
game_lifecycle_after_game
stopping_park_rc=$?
set -e
[ "$stopping_park_rc" -eq 3 ]
[ -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]

python3 - "$GAME_LIFECYCLE_DIR/control.json" "$request_id" <<'PY'
import json
import sys

path, rid = sys.argv[1], sys.argv[2]
request = json.load(open(path.replace("control.json", "request.json"), encoding="utf-8"))
control = dict(request, action="stop")
with open(path, "w", encoding="utf-8") as stream:
    json.dump(control, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
PY
node --input-type=module -e "
import { readGameLifecycleControl, writeGameLifecycleResource } from './lib/game_lifecycle.mjs';
const control = readGameLifecycleControl('$GAME_LIFECYCLE_DIR');
const resource = writeGameLifecycleResource(control, 'stopped', { quit_called: true }, '$GAME_LIFECYCLE_DIR');
if (!resource) process.exit(1);
" >/dev/null 2>&1
set +e
game_lifecycle_resume_pending
stopping_finish_rc=$?
set -e
[ "$stopping_finish_rc" -eq 0 ]
grep -qx finish "$events"
[ "$(game_lifecycle_ack_status)" = "stopped" ]
[ -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]
game_lifecycle_restore_loop
[ ! -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]

# A lifecycle stop has only 30 seconds to confirm that the watchdog exited.
# The watchdog's normal 60-second polling wait must therefore be interruptible:
# bash defers a TERM trap while a foreground sleep is still running.
watchdog_root="$test_root/watchdog"
mkdir -p "$watchdog_root/lib" "$watchdog_root/tmp/state/game_lifecycle"
cp "$repo_root/soviet_watchdog.sh" "$watchdog_root/"
cp "$repo_root/lib/game_lifecycle.sh" "$repo_root/lib/game_lifecycle.py" "$repo_root/lib/game_terminal.py" "$watchdog_root/lib/"
python3 - "$watchdog_root/tmp/state/game_lifecycle/request.json" "$watchdog_root/tmp/state/game_lifecycle/ack.json" <<'PY'
import json
import sys
import time

request = {
    "schema": 1,
    "request_id": "4e67742b-b543-4380-9202-27eca7d004ef",
    "game": "sorengame",
    "generation": 1,
    "deadline_epoch": time.time() - 60,
    "deadline_at": "2030-01-01T00:00:00.000Z",
}
ack = dict(request, status="stopped")
for path, value in zip(sys.argv[1:], (request, ack)):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
PY
(
	cd "$watchdog_root"
	SOVIET_WATCHDOG_INTERVAL=60 ./soviet_watchdog.sh >watchdog.log 2>&1
) &
watchdog_launcher_pid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
	grep -q 'game-only lifecycle handover中' "$watchdog_root/watchdog.log" 2>/dev/null && break
	sleep 0.1
done
[ -f "$watchdog_root/tmp/state/.soviet_watchdog.lock/owner" ]
grep -q 'game-only lifecycle handover中' "$watchdog_root/watchdog.log"
watchdog_pid=$(cat "$watchdog_root/tmp/state/.soviet_watchdog.lock/owner")
kill -TERM "$watchdog_pid"
watchdog_waited=0
while kill -0 "$watchdog_pid" 2>/dev/null && [ "$watchdog_waited" -lt 50 ]; do
	sleep 0.1
	watchdog_waited=$((watchdog_waited + 1))
done
if kill -0 "$watchdog_pid" 2>/dev/null; then
	echo "watchdog did not respond to TERM within 5 seconds" >&2
	exit 1
fi
wait "$watchdog_launcher_pid" 2>/dev/null || true
unset watchdog_pid
unset watchdog_launcher_pid

# The committed player snapshot is re-read every game by soren_loop.sh (see the
# main-loop reload), so a player_change committed at a game boundary must apply
# to the next game without restarting the long-lived loop.  The reload has to be
# idempotent inside one process and fail closed to existing on a corrupt or
# missing snapshot.
player_state_path="$GAME_LIFECYCLE_DIR/player_state.json"
GAME_LIFECYCLE_PLAYER_STATE_FILE="$player_state_path"
write_player_state() {
	local policy="$1" generation="$2" game_generation="$3" run_id="$4"
	python3 - "$player_state_path" "$policy" "$generation" "$game_generation" "$run_id" <<'PY'
import json
import sys

path, policy, generation, game_generation, run_id = sys.argv[1:6]
value = {
    "schema": 1,
    "game": "sorengame",
    "policy": policy,
    "player_generation": int(generation),
    "game_generation": int(game_generation) if game_generation else None,
    "run_id": run_id,
    "config_hash": "a" * 64,
    "source_request_id": run_id,
    "updated_at": "2030-01-01T00:00:00.000Z",
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
PY
}

unset SOREN_PLAYER_POLICY SOREN_JEV_RUN_ID SOREN_JEV_PLAYER_GENERATION SOREN_JEV_GAME_GENERATION JEV_PLAYER_ENABLED
write_player_state existing 2 "" ""
game_lifecycle_load_player_policy
[ "${SOREN_PLAYER_POLICY:-}" = "existing" ]
[ "${JEV_PLAYER_ENABLED:-}" = "0" ]
[ -z "${SOREN_JEV_RUN_ID:-}" ]

jev_run_id="ca834b7a-d0f7-4b74-9d9a-f3182855f2a7"
write_player_state jev 3 1 "$jev_run_id"
game_lifecycle_load_player_policy
[ "${SOREN_PLAYER_POLICY:-}" = "jev" ]
[ "${JEV_PLAYER_ENABLED:-}" = "1" ]
[ "${SOREN_JEV_RUN_ID:-}" = "$jev_run_id" ]
[ "${SOREN_JEV_PLAYER_GENERATION:-}" = "3" ]
[ "${SOREN_JEV_GAME_GENERATION:-}" = "1" ]
[ "${JEV_MAX_REQUESTS_PER_RUN:-}" = "500" ]

# The same process must switch back to existing without leaking JEV identity.
write_player_state existing 4 1 "$jev_run_id"
game_lifecycle_load_player_policy
[ "${SOREN_PLAYER_POLICY:-}" = "existing" ]
[ "${JEV_PLAYER_ENABLED:-}" = "0" ]
[ -z "${SOREN_JEV_RUN_ID:-}" ]
[ -z "${SOREN_JEV_GAME_GENERATION:-}" ]

# A corrupt snapshot fails closed even when JEV was active a moment ago.
write_player_state jev 5 1 "$jev_run_id"
game_lifecycle_load_player_policy
printf '%s\n' '{"schema":1,"game":"sorengame","policy":"bogus"}' >"$player_state_path"
game_lifecycle_load_player_policy || true
[ "${SOREN_PLAYER_POLICY:-}" = "existing" ]
[ "${JEV_PLAYER_ENABLED:-}" = "0" ]
[ -z "${SOREN_JEV_RUN_ID:-}" ]
rm -f "$player_state_path"

# A one-game JEV park that is not yet stable must be retried rather than
# silently skipped: skipping it let the supervisor respawn and start a second
# JEV game under the same explicit start.  The mark helper runs in a command
# substitution, so count calls in a file rather than a shell variable.
_jev_mark_calls_file="$test_root/jev_mark_calls"
: >"$_jev_mark_calls_file"
rm -f "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json"
rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE"
GAME_LIFECYCLE_JEV_ONE_GAME_FILE="$GAME_LIFECYCLE_DIR/jev_one_game.json"
game_lifecycle_mark_jev_one_game() {
	echo x >>"$_jev_mark_calls_file"
	printf '%s\n' '{"schema":1,"game":"sorengame","policy":"jev","run_id":"22222222-2222-4222-8222-222222222222"}' >"$GAME_LIFECYCLE_JEV_ONE_GAME_FILE"
	[ "$(wc -l <"$_jev_mark_calls_file" | tr -d ' ')" -ge 2 ] && return 0
	return 1
}
game_lifecycle_jev_complete
[ "$(wc -l <"$_jev_mark_calls_file" | tr -d ' ')" -ge 2 ]
# A long-lived supervisor that predates the dedicated predicate must still be
# suppressed, so the one-game park also creates the generic loop pause marker.
[ -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]

# A terminal failure is not retried forever.
: >"$_jev_mark_calls_file"
game_lifecycle_mark_jev_one_game() {
	echo x >>"$_jev_mark_calls_file"
	return 3
}
set +e
game_lifecycle_jev_complete
jev_terminal_rc=$?
set -e
[ "$jev_terminal_rc" -eq 3 ]
[ "$(wc -l <"$_jev_mark_calls_file" | tr -d ' ')" -eq 1 ]

echo "game lifecycle shell tests passed"
