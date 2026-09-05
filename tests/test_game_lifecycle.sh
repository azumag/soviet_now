#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/game-lifecycle-shell.XXXXXX")"
cleanup() {
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
	boundary) set_ack boundary; return 0 ;;
	stop) set_ack stop_requested; return 0 ;;
	finish) set_ack stopped; return 0 ;;
	cancel) set_ack cancelled; return 0 ;;
	*) return 0 ;;
	esac
}
_game_lifecycle_wait_resource() { printf '%s\n' stopped >"$resource_status_file"; return 0; }

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

echo "game lifecycle shell tests passed"
