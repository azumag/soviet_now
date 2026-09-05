#!/bin/bash
# game_lifecycle.sh - game-only handover broker for the long-lived Soren loop.
#
# This file is sourced by eloop_lib.sh.  It deliberately owns only the Soren
# game loop, its improvement workers, and the durable handover files.  The
# browser overlays, audio workers, and streaming encoder are outside this
# scope and are never stopped here.

GAME_LIFECYCLE_ROOT="${ELOOP_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TMP_STATE_DIR="${TMP_STATE_DIR:-tmp/state}"
GAME_LIFECYCLE_PY="${GAME_LIFECYCLE_PY:-$GAME_LIFECYCLE_ROOT/lib/game_lifecycle.py}"
GAME_LIFECYCLE_DIR="${GAME_LIFECYCLE_DIR:-$GAME_LIFECYCLE_ROOT/tmp/state/game_lifecycle}"
GAME_LIFECYCLE_ENABLED="${GAME_LIFECYCLE_ENABLED:-1}"
GAME_LIFECYCLE_RESOURCE_WAIT_SEC="${GAME_LIFECYCLE_RESOURCE_WAIT_SEC:-60}"
GAME_LIFECYCLE_POLL_SEC="${GAME_LIFECYCLE_POLL_SEC:-1}"
GAME_LIFECYCLE_IMPROVE_PAUSE_FILE="$GAME_LIFECYCLE_DIR/improvement_pause.json"
GAME_LIFECYCLE_LOOP_PAUSE_FILE="$GAME_LIFECYCLE_ROOT/tmp/state/soren_loop.paused"
GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE="$GAME_LIFECYCLE_DIR/loop_pause.json"

_game_lifecycle_log() {
	if command -v log >/dev/null 2>&1; then
		log "$*"
	else
		printf '[game-lifecycle] %s\n' "$*" >&2
	fi
}

_game_lifecycle_cli() {
	[ -f "$GAME_LIFECYCLE_PY" ] || return 4
	python3 "$GAME_LIFECYCLE_PY" --root "$GAME_LIFECYCLE_ROOT" "$@"
}

_game_lifecycle_json_field() {
	local path="$1" field="$2"
	python3 - "$path" "$field" <<'PY'
import json
import sys

try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(value, dict):
    raise SystemExit(1)
field = sys.argv[2]
result = value.get(field)
if result is None:
    raise SystemExit(1)
if isinstance(result, bool):
    print("true" if result else "false")
else:
    print(str(result))
PY
}

game_lifecycle_request_id() {
	_game_lifecycle_json_field "$GAME_LIFECYCLE_DIR/request.json" request_id 2>/dev/null
}

game_lifecycle_ack_status() {
	_game_lifecycle_json_field "$GAME_LIFECYCLE_DIR/ack.json" status 2>/dev/null
}

game_lifecycle_resource_status() {
	_game_lifecycle_json_field "$GAME_LIFECYCLE_DIR/game_resource.json" status 2>/dev/null
}

_game_lifecycle_control_action() {
	local request_id="${1:-}"
	python3 - "$GAME_LIFECYCLE_DIR/control.json" "$request_id" <<'PY'
import json
import sys

try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(value, dict) or value.get("request_id") != sys.argv[2]:
    raise SystemExit(1)
action = value.get("action")
if action:
    print(action)
PY
}

# A game-only bridge is intentionally absent while this exact lifecycle
# operation is closing resources, or while a stopped bridge awaits an explicit
# fresh launch for resume.  The watchdog and start_all use this predicate to
# avoid treating an intentional handover as a crash.  All identity fields are
# compared so an old request cannot suppress recovery for a later game.
game_lifecycle_bridge_parked() {
	[ "${GAME_LIFECYCLE_ENABLED:-1}" = "1" ] || return 1
	python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys
import time

try:
    request = json.load(open(sys.argv[1], encoding="utf-8"))
    ack = json.load(open(sys.argv[2], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(request, dict) or not isinstance(ack, dict):
    raise SystemExit(1)
if request.get("schema") != 1 or ack.get("schema") != 1:
    raise SystemExit(1)
for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
    if field not in request or ack.get(field) != request.get(field):
        raise SystemExit(1)
status = ack.get("status")
if status == "stopped":
    # Terminal: a stopped bridge awaits an explicit fresh launch for resume.
    raise SystemExit(0)
if status in {"stop_requested", "resume_requested", "stopping"}:
    # Non-terminal parks expire with the request deadline.  A stale park must
    # not suppress watchdog recovery forever while the bridge treats the same
    # request as expired.
    try:
        deadline = float(request.get("deadline_epoch"))
    except (TypeError, ValueError):
        raise SystemExit(1)
    if deadline > time.time():
        raise SystemExit(0)
    raise SystemExit(1)
raise SystemExit(1)
PY
}

_game_lifecycle_write_record() {
	local path="$1" request_id="$2" improvement_created="$3" daemon_pid="$4" child_pid="$5"
	local loop_created="${6:-0}"
	python3 - "$path" "$request_id" "$improvement_created" "$daemon_pid" "$child_pid" "$loop_created" <<'PY'
import json
import os
import sys
import tempfile
import time
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
os.chmod(path.parent, 0o700)
value = {
    "schema": 1,
    "request_id": sys.argv[2],
    "improvement_marker_created": sys.argv[3] == "1",
    "improve_daemon_pid": int(sys.argv[4]) if sys.argv[4].isdigit() else None,
    "improve_child_pid": int(sys.argv[5]) if sys.argv[5].isdigit() else None,
    "loop_marker_created": sys.argv[6] == "1",
    "updated_at": int(time.time()),
}
fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass
PY
}

_game_lifecycle_read_pid() {
	local path="$1"
	local pid
	pid=$(cat "$path" 2>/dev/null || true)
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	0) return 1 ;;
	*) printf '%s\n' "$pid" ;;
	esac
}

_game_lifecycle_is_improve_daemon_pid() {
	local pid="$1" command_line
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	kill -0 "$pid" 2>/dev/null || return 1
	command_line=$(ps -p "$pid" -o command= 2>/dev/null || true)
	[ -n "$command_line" ] || return 1
	echo "$command_line" | grep -Eq '(^|[[:space:]/])improve_daemon[.]sh([[:space:]]|$)'
}

# Mutual exclusion with the Python broker (which holds fcntl flock LOCK_EX on
# the same broker.lock for every mutating command).  The flock(1) binary is
# preferred; where it is unavailable (e.g. stock macOS) the same flock(2)
# exclusive lock is taken via python3 on an inherited fd 9, which names the
# identical lock file so both lock holders contend.  Only when neither
# mechanism exists do we fail closed without touching any marker.
_game_lifecycle_lock_acquire() {
	local lock_file="$1" timeout_sec="${GAME_LIFECYCLE_LOCK_TIMEOUT_SEC:-30}"
	case "$timeout_sec" in ''|*[!0-9]*) timeout_sec=30 ;; esac
	mkdir -p "$(dirname "$lock_file")" 2>/dev/null || return 1
	# Shellcheck SC2094: fd 9 is our dedicated lifecycle lock descriptor.
	# exec redirections persist for the whole shell, so the stderr guard must
	# be scoped to the group; a bare `exec ... 2>/dev/null` would silence the
	# caller's stderr (and this library's _game_lifecycle_log) forever.
	if ! { exec 9>>"$lock_file"; } 2>/dev/null; then
		return 1
	fi
	local waited=0
	if command -v flock >/dev/null 2>&1; then
		while ! flock -n 9 2>/dev/null; do
			[ "$waited" -ge "$timeout_sec" ] && { { exec 9>&-; } 2>/dev/null || true; return 1; }
			sleep 1
			waited=$((waited + 1))
		done
		return 0
	fi
	command -v python3 >/dev/null 2>&1 || { { exec 9>&-; } 2>/dev/null || true; return 1; }
	while ! python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; do
		[ "$waited" -ge "$timeout_sec" ] && { { exec 9>&-; } 2>/dev/null || true; return 1; }
		sleep 1
		waited=$((waited + 1))
	done
	return 0
}

_game_lifecycle_lock_release() {
	if command -v flock >/dev/null 2>&1; then
		flock -u 9 2>/dev/null || true
	else
		python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_UN)' 2>/dev/null || true
	fi
	{ exec 9>&-; } 2>/dev/null || true
}

# Whether an existing pause record proves that this same request already
# created the marker (an earlier pause attempt of the same handover).  A
# repeat pause must keep that ownership: rewriting the record with
# marker_created=false would leak the gate forever, because the later restore
# would then treat a lifecycle-created pause as operator-owned.  A marker with
# no matching record is operator-owned (or another request's) and is never
# adopted here.
_game_lifecycle_pause_record_claims_marker() {
	local record="$1" field="$2" request_id="$3"
	[ -f "$record" ] || return 1
	[ "$(_game_lifecycle_json_field "$record" request_id 2>/dev/null || true)" = "$request_id" ] || return 1
	[ "$(_game_lifecycle_json_field "$record" "$field" 2>/dev/null || true)" = "true" ] || return 1
	return 0
}

_game_lifecycle_pause_improvements() {
	local request_id="${1:-}"
	[ -n "$request_id" ] || return 1
	_game_lifecycle_lock_acquire "$GAME_LIFECYCLE_DIR/broker.lock" || {
		_game_lifecycle_log "改善プロセスの排他ロックを取得できません (request=$request_id)。マーカーを作成せず失敗します"
		return 1
	}
	_game_lifecycle_pause_improvements_locked "$request_id"
	local rc=$?
	_game_lifecycle_lock_release
	return $rc
}

_game_lifecycle_pause_improvements_locked() {
	local request_id="${1:-}"
	[ -n "$request_id" ] || return 1
	mkdir -p "$GAME_LIFECYCLE_DIR" 2>/dev/null || return 1

	local marker_created=0
	if [ -e "$TMP_STATE_DIR/improve_daemon.paused" ]; then
		if _game_lifecycle_pause_record_claims_marker "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" improvement_marker_created "$request_id"; then
			marker_created=1
		fi
	else
		(umask 077; : >"$TMP_STATE_DIR/improve_daemon.paused") || return 1
		marker_created=1
	fi

	local child_pid="" daemon_pid=""
	if command -v _find_live_improve_pid >/dev/null 2>&1; then
		child_pid=$(_find_live_improve_pid 2>/dev/null || true)
	fi
	case "$child_pid" in
	''|0|*[!0-9]*) child_pid="" ;;
	esac
	if [ -n "$child_pid" ] && command -v _stop_improve_pid_if_running >/dev/null 2>&1; then
		# _find_live_improve_pid validates the command as eloop_improve*.sh.
		# Its stop helper also scopes and drains the AI descendants before the
		# parent, so no unrelated worker is selected by this handover.
		if ! _stop_improve_pid_if_running "$child_pid" "game_lifecycle_improve_child"; then
			[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
			return 1
		fi
	fi

	local daemon_pid_file="${IMPROVE_DAEMON_PID_FILE:-$TMP_STATE_DIR/improve_daemon.pid}"
	daemon_pid=$(_game_lifecycle_read_pid "$daemon_pid_file" 2>/dev/null || true)
	if [ -n "$daemon_pid" ]; then
		if _game_lifecycle_is_improve_daemon_pid "$daemon_pid"; then
			if [ "$daemon_pid" = "$$" ]; then
				_game_lifecycle_log "improve_daemon PID=$daemon_pid は現在の loop と同一 → 停止しません"
				[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
				return 1
			fi
			_game_lifecycle_log "改善デーモンを停止 (PID=$daemon_pid, request=$request_id)"
			if command -v _stop_pid_with_fallback >/dev/null 2>&1; then
				_stop_pid_with_fallback "$daemon_pid" "game_lifecycle_improve_daemon"
			else
				kill "$daemon_pid" 2>/dev/null || true
			fi
			if kill -0 "$daemon_pid" 2>/dev/null; then
				_game_lifecycle_log "改善デーモン停止を確認できません (PID=$daemon_pid)"
				[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
				return 1
			fi
		elif kill -0 "$daemon_pid" 2>/dev/null; then
			# A live PID-file owner with a different command is never killed.
			_game_lifecycle_log "改善デーモンPIDファイルが別プロセスを指すため停止を拒否 (PID=$daemon_pid)"
			[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
			return 1
		fi
	fi

	# A late child spawn racing the daemon stop is still accepted only when the
	# existing improve helper can positively identify it.  One bounded retry is
	# enough; a new child after this point means the pause gate did not hold.
	local late_child=""
	if command -v _find_live_improve_pid >/dev/null 2>&1; then
		late_child=$(_find_live_improve_pid 2>/dev/null || true)
	fi
	case "$late_child" in
	''|0|*[!0-9]*) late_child="" ;;
	esac
	if [ -n "$late_child" ]; then
		_game_lifecycle_log "改善子ジョブが停止後に再出現 (PID=$late_child)"
		[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
		return 1
	fi

	_game_lifecycle_write_record "$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE" "$request_id" "$marker_created" "$daemon_pid" "$child_pid" 0 || {
		[ "$marker_created" -eq 1 ] && rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
		return 1
	}
	return 0
}

game_lifecycle_restore_improvements() {
	local record="$GAME_LIFECYCLE_IMPROVE_PAUSE_FILE"
	[ -f "$record" ] || return 0
	local marker_created
	marker_created=$(_game_lifecycle_json_field "$record" improvement_marker_created 2>/dev/null || echo false)
	if [ "$marker_created" = "true" ]; then
		rm -f "$TMP_STATE_DIR/improve_daemon.paused" 2>/dev/null || true
	fi
	rm -f "$record" 2>/dev/null || true
	return 0
}

_game_lifecycle_pause_loop() {
	local request_id="${1:-}" marker_created=0
	[ -n "$request_id" ] || return 1
	mkdir -p "$GAME_LIFECYCLE_DIR" "$TMP_STATE_DIR" 2>/dev/null || return 1
	if [ -e "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" ]; then
		# Same ownership rule as the improve pause: a repeat pause of the same
		# request must not rewrite the record into claiming the marker was
		# pre-existing, or game_lifecycle_restore_loop would leak the park.
		if _game_lifecycle_pause_record_claims_marker "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" loop_marker_created "$request_id"; then
			marker_created=1
		fi
	else
		(umask 077; : >"$GAME_LIFECYCLE_LOOP_PAUSE_FILE") || return 1
		marker_created=1
	fi
	_game_lifecycle_write_record "$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE" "$request_id" 0 "" "" "$marker_created"
}

game_lifecycle_restore_loop() {
	local record="$GAME_LIFECYCLE_LOOP_PAUSE_STATE_FILE"
	[ -f "$record" ] || return 0
	local marker_created
	marker_created=$(_game_lifecycle_json_field "$record" loop_marker_created 2>/dev/null || echo false)
	if [ "$marker_created" = "true" ]; then
		rm -f "$GAME_LIFECYCLE_LOOP_PAUSE_FILE" 2>/dev/null || true
	fi
	rm -f "$record" 2>/dev/null || true
	return 0
}

_game_lifecycle_wait_resource() {
	local request_id="$1" wait_sec="${GAME_LIFECYCLE_RESOURCE_WAIT_SEC:-60}" poll_sec="${GAME_LIFECYCLE_POLL_SEC:-1}"
	case "$wait_sec" in ''|*[!0-9]*) wait_sec=60 ;; esac
	case "$poll_sec" in ''|*[!0-9]*) poll_sec=1 ;; esac
	local deadline=$(( $(date +%s) + wait_sec )) status
	while [ "$(date +%s)" -le "$deadline" ]; do
		status=$(python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/game_resource.json" "$request_id" <<'PY'
import json
import sys
try:
    request = json.load(open(sys.argv[1], encoding="utf-8"))
    value = json.load(open(sys.argv[2], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(request, dict) or not isinstance(value, dict):
    raise SystemExit(1)
if request.get("schema") != 1 or value.get("schema") != 1:
    raise SystemExit(1)
if value.get("request_id") != sys.argv[3]:
    raise SystemExit(1)
for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
    if field not in request or value.get(field) != request.get(field):
        raise SystemExit(1)
if value.get("status") not in {"stopped", "failed", "unsupported", "cancelled"}:
    raise SystemExit(1)
print(value.get("status", ""))
PY
)
		case "$status" in
		stopped) return 0 ;;
		failed|unsupported|cancelled) return 2 ;;
		esac
		sleep "$poll_sec"
	done
	return 1
}

_game_lifecycle_stop_from_boundary() {
	local request_id="$1" ack_status="${2:-}"
	[ -n "$request_id" ] || return 1
	case "$ack_status" in
	boundary|stop_requested|stopped) ;;
	*) return 1 ;;
	esac

	if [ "$ack_status" != "stopped" ]; then
		if ! _game_lifecycle_pause_improvements "$request_id"; then
			_game_lifecycle_log "改善プロセスの停止確認に失敗。request=$request_id をキャンセルして旧ゲームを継続"
			_game_lifecycle_cli cancel --request-id "$request_id" >/dev/null 2>&1 || true
			game_lifecycle_restore_improvements
			game_lifecycle_restore_loop
			return 1
		fi
	fi

	local stop_output stop_rc
	stop_output=$(_game_lifecycle_cli stop --request-id "$request_id" 2>/dev/null)
	stop_rc=$?
	case "$stop_rc" in
	0) ;;
	2)
		_game_lifecycle_log "停止要求の期限切れ。旧ゲームを継続 (request=$request_id)"
		game_lifecycle_restore_improvements
		game_lifecycle_restore_loop
		return 1
		;;
	*)
		_game_lifecycle_log "停止要求を確定できません。旧ゲームを保留 (request=$request_id rc=$stop_rc output=${stop_output:-none})"
		_game_lifecycle_pause_loop "$request_id" || true
		return 2
		;;
	esac

	local resource_rc
	_game_lifecycle_wait_resource "$request_id"
	resource_rc=$?
	if [ "$resource_rc" -ne 0 ]; then
		local resource_status
		resource_status=$(game_lifecycle_resource_status 2>/dev/null || true)
		if [ "$resource_status" = "unsupported" ]; then
			# Legacy bridge or a not-yet-ready shared overlay must keep the old
			# game alive.  Cancel this exact request and let normal play continue;
			# do not turn an unsupported capability into a stop.
			_game_lifecycle_cli cancel --request-id "$request_id" >/dev/null 2>&1 || true
			game_lifecycle_restore_improvements
			game_lifecycle_restore_loop
			_game_lifecycle_log "共有表示未準備/legacy bridge のため handover をキャンセルし、旧ゲームを継続 (request=$request_id)"
			return 1
		fi
		_game_lifecycle_log "ゲーム資源停止の確認待ちを終了 (request=$request_id rc=$resource_rc)。共通表示は維持"
		_game_lifecycle_pause_loop "$request_id" || true
		return 2
	fi

	local finish_output finish_rc
	finish_output=$(_game_lifecycle_cli finish --request-id "$request_id" 2>/dev/null)
	finish_rc=$?
	if [ "$finish_rc" -ne 0 ]; then
		_game_lifecycle_log "ゲーム資源停止後の broker 確定に失敗。旧ループを保留 (request=$request_id rc=$finish_rc output=${finish_output:-none})"
		_game_lifecycle_pause_loop "$request_id" || true
		return 2
	fi
	_game_lifecycle_pause_loop "$request_id" || return 2
	_game_lifecycle_log "旧ゲームを試合境界で停止し、ループを一時停止 (request=$request_id)。共通表示/音声/配信は維持"
	return 0
}

# Read request.json and ack.json in a single python3 invocation and emit
# "request_id<TAB>ack_status".  Fails when either file is missing/invalid or
# when the ack does not carry the request's full identity, so callers can
# never mix a request from one generation with an ack from another (TOCTOU).
_game_lifecycle_request_ack_pair() {
	python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys

try:
    request = json.load(open(sys.argv[1], encoding="utf-8"))
    ack = json.load(open(sys.argv[2], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(request, dict) or not isinstance(ack, dict):
    raise SystemExit(1)
if request.get("schema") != 1 or ack.get("schema") != 1:
    raise SystemExit(1)
for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
    if field not in request or ack.get(field) != request.get(field):
        raise SystemExit(1)
request_id = request.get("request_id")
status = ack.get("status")
if not isinstance(request_id, str) or not request_id:
    raise SystemExit(1)
if not isinstance(status, str) or not status:
    raise SystemExit(1)
if "\n" in request_id or "\t" in request_id or "\n" in status or "\t" in status:
    raise SystemExit(1)
print(f"{request_id}\t{status}")
PY
}

game_lifecycle_after_game() {
	[ "$GAME_LIFECYCLE_ENABLED" = "1" ] || return 1
	local request_id ack_status boundary_output boundary_rc pair
	pair=$(_game_lifecycle_request_ack_pair 2>/dev/null) || return 1
	request_id="${pair%%$'\t'*}"
	ack_status="${pair#*$'\t'}"
	[ -n "$request_id" ] || return 1
	[ -n "$ack_status" ] || return 1
	case "$ack_status" in
	accepted|waiting)
		boundary_output=$(_game_lifecycle_cli boundary --request-id "$request_id" 2>/dev/null)
		boundary_rc=$?
		case "$boundary_rc" in
		0)
			# Re-read both records together and confirm the request did not
			# change under us before parking the loop on the new status.
			pair=$(_game_lifecycle_request_ack_pair 2>/dev/null) || return 1
			[ "${pair%%$'\t'*}" = "$request_id" ] || return 1
			ack_status="${pair#*$'\t'}"
			;;
		1)
			# The current one-game unit is not terminal yet.  Keep the outer
			# loop from retrying, but do not stop resources or improvements;
			# an explicit cancel can remove this park and resume the game.
			_game_lifecycle_log "試合終了境界をまだ確認できないため次ゲームを保留 (request=$request_id)"
			_game_lifecycle_pause_loop "$request_id" || true
			return 3
			;;
		2|3|4)
			_game_lifecycle_log "試合終了境界要求を完了できません。通常運転へ戻します (request=$request_id rc=$boundary_rc output=${boundary_output:-none})"
			return 1
			;;
		esac
		;;
	boundary|stop_requested|stopped) ;;
	resume_requested|cancelled|failed|timeout|unsupported|"" ) return 1 ;;
	*) return 1 ;;
	esac
	case "$ack_status" in
	boundary|stop_requested|stopped)
		# Boundary acknowledgement only parks the loop.  An explicit stop
		# control, issued by the coordinator/adapter after its writer-side
		# quiesce decision, is required before any game resource is closed.
		_game_lifecycle_pause_loop "$request_id" || return 2
		_game_lifecycle_log "試合終了境界を確認し、次ゲームだけを保留 (request=$request_id)。資源停止は明示stop待ち"
		return 3
		;;
	*) return 1 ;;
	esac
}

# Explicit irreversible half of a handover.  The coordinator calls this only
# after the boundary receipt has been observed and its writer-side quiesce
# decision has committed.  Keeping it separate from game_lifecycle_after_game
# ensures that a boundary wait never closes a still-live game page.
game_lifecycle_stop_after_boundary() {
	[ "$GAME_LIFECYCLE_ENABLED" = "1" ] || return 1
	local request_id ack_status pair
	pair=$(_game_lifecycle_request_ack_pair 2>/dev/null) || return 1
	request_id="${pair%%$'\t'*}"
	ack_status="${pair#*$'\t'}"
	[ -n "$request_id" ] || return 1
	[ -n "$ack_status" ] || return 1
	case "$ack_status" in
	boundary|stop_requested|stopped)
		_game_lifecycle_stop_from_boundary "$request_id" "$ack_status"
		return $?
		;;
	*) return 1 ;;
	esac
}

# Used by a reloaded eloop.sh after a controller crash between boundary and
# stop.  It deliberately reuses the same request/identity instead of creating
# a second operation, so a late response cannot stop another game.
game_lifecycle_resume_pending() {
	[ "$GAME_LIFECYCLE_ENABLED" = "1" ] || return 1
	local status request_id
	status=$(game_lifecycle_ack_status 2>/dev/null || true)
	case "$status" in
	accepted|waiting)
		game_lifecycle_after_game
		return $?
		;;
	cancelled|resumed)
		# The request was explicitly rolled back (or the live bridge completed an
		# in-process resume).  Remove only markers created by this handover; an
		# operator's pre-existing pause remains untouched.
		game_lifecycle_restore_improvements
		game_lifecycle_restore_loop
		return 1
		;;
	resume_requested)
		# A live bridge may still be clearing its page/audio flags.  Restore the
		# shell gates now, but never start a fresh game from this recovery path.
		game_lifecycle_restore_improvements
		game_lifecycle_restore_loop
		return 3
		;;
	boundary)
		# A prior controller may have exited after parking.  Preserve that
		# park without implicitly converting recovery into a stop.
		request_id=$(game_lifecycle_request_id 2>/dev/null || true)
		[ -n "$request_id" ] || return 1
		_game_lifecycle_pause_loop "$request_id" || return 2
		return 3
		;;
	stop_requested|stopped)
		game_lifecycle_stop_after_boundary
		return $?
		;;
	*) return 1 ;;
	esac
}
