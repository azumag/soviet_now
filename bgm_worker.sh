#!/bin/bash
# 天気・論文・CLIゲーム等の共通BGM。半熟英雄・特別企画には重ねない。
# 本編の停止が確認できない間は再生しない。Soren自身のBGMは既存経路を維持する。
# canonicalに古いCLIゲーム名が残っても、SorenのBGMへ重ねない。
# DOCICH_CANONICAL / SOREN_BGM_FILE / SOREN_BGM_SINK / SOREN_BGM_VOLUME は従来通り。
# SOREN_BGM_STATE_DIR は既存Soren tmp/stateを参照する（別のlifecycleは作らない）。
# systemd soren-bgm.service用。コード配備後はこのworkerだけの再起動が必要。

BGM_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${DOCICH_CANONICAL:-/home/ubuntu/docich/run-soren-live/game_switch.json}"
BGM_FILE="${SOREN_BGM_FILE:-/home/ubuntu/soren/sorengame/assets/BGM/インターナショナル.ogg}"
SINK="${SOREN_BGM_SINK:-soren_null}"
# The system service has no XDG_RUNTIME_DIR. SDL/libpulse otherwise autospawns
# a private server under /tmp, whose sink is not captured by the encoder.
# Honor an explicit operator endpoint; never start/restart an audio server.
BGM_PULSE_SERVER="${PULSE_SERVER:-unix:/run/user/$UID/pulse/native}"
VOL="${SOREN_BGM_VOLUME:-25}"
STATE_DIR="${SOREN_BGM_STATE_DIR:-$BGM_SCRIPT_DIR/tmp/state}"
source "$BGM_SCRIPT_DIR/lib/poll_wait.sh" 2>/dev/null || docich_poll_sleep() { sleep "${1:-1}" 2>/dev/null || true; }
TAG="soren-bgm-loop"
PLAYER_PID=""
LAST_REPORT=""
LAST_REPORT_AT=-30
PLAYER_EXITS=0
LAST_EXIT=""

cli_game_active() {
	[ -f "$BGM_FILE" ] || { echo audio_missing; return 1; }
	python3 - "$CANONICAL" "$STATE_DIR" <<'PY'
import json
import math
import os
from pathlib import Path
import stat
import sys
import uuid


def read_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("unbounded state")
    return data


try:
    root = Path(sys.argv[2])
    paths = [Path(sys.argv[1]), root / "game_lifecycle/request.json",
             root / "game_lifecycle/ack.json", root / "soren_loop.paused"]
    raw = []
    for path, reason in zip(paths, ("canonical_unreadable", "request_unreadable",
                                    "ack_unreadable", "pause_unreadable")):
        try:
            raw.append(read_file(path))
        except (OSError, ValueError):
            print(reason)
            raise SystemExit(1)
    canonical, request, ack = [json.loads(data) for data in raw[:3]]
    if not all(isinstance(item, dict) for item in (canonical, request, ack)):
        raise ValueError("invalid_state")
    active = canonical.get("active")
    game = active.get("game") if isinstance(active, dict) else None
    # All ordinary corners (including synthetic program views) get BGM.
    # Native Soren owns its music; Hanjuku and the OBS/SRT special corner
    # retain their own audio without the common fallback mixed over it.
    if canonical.get("phase") != "ready":
        raise ValueError("switch_not_ready")
    if not isinstance(game, str) or not game:
        raise ValueError("invalid_game")
    if game in {"sorengame", "soren91", "hanjuku-hero", "external-video-view"}:
        raise ValueError("excluded_game")
    # A matching terminal stop is positive proof; a CLI name or pause marker
    # alone is not. Terminal stopped acknowledgements intentionally have no TTL.
    if (type(request.get("schema")) is not int or request["schema"] != 1
            or type(ack.get("schema")) is not int or ack["schema"] != 1
            or request.get("game") != "sorengame" or ack.get("status") != "stopped"):
        raise ValueError("native_stop_unconfirmed")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
        raise ValueError("invalid_request")
    if type(request.get("generation")) is not int or request["generation"] < 1:
        raise ValueError("invalid_generation")
    deadline = request.get("deadline_epoch")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("invalid_deadline")
    if not isinstance(request.get("deadline_at"), str) or not request["deadline_at"]:
        raise ValueError("invalid_deadline")
    ack_deadline = ack.get("deadline_epoch")
    if (type(ack_deadline) not in (int, float) or not math.isfinite(ack_deadline)
            or ack_deadline != deadline):
        raise ValueError("ack_mismatch")
    for field in ("request_id", "game", "generation", "deadline_at"):
        if type(ack.get(field)) is not type(request[field]) or ack[field] != request[field]:
            raise ValueError("ack_mismatch")
    # Reject a handover that changed during this read. No marker, receipt,
    # canonical state, game process or shared audio resource is changed here.
    if any(read_file(path) != data for path, data in zip(paths, raw)):
        raise ValueError("handover_changed")
except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
    # Never emit parser/OS error text, filenames or state content.
    reasons = {"invalid_state", "switch_not_ready", "invalid_game", "excluded_game",
               "native_stop_unconfirmed", "invalid_request", "invalid_generation",
               "invalid_deadline", "ack_mismatch", "handover_changed"}
    print(str(exc) if type(exc) is ValueError and str(exc) in reasons else "invalid_state")
    raise SystemExit(1)
print("eligible")
PY
}

report_state() {
	local state="$1" reason="$2" signature="$1:$2:$LAST_EXIT"
	# Transitions go to the journal; the small private snapshot has a bounded
	# heartbeat. Neither player stderr nor raw environment is recorded.
	if [ "$signature" != "$LAST_REPORT" ]; then
		printf 'bgm_worker state=%s reason=%s last_exit=%s\n' "$state" "$reason" "${LAST_EXIT:-none}"
	elif (( SECONDS - LAST_REPORT_AT < 30 )); then
		return 0
	fi
	LAST_REPORT="$signature"
	LAST_REPORT_AT=$SECONDS
	python3 - "$STATE_DIR/bgm_worker.json" "$$" "${PLAYER_PID:-0}" "$state" "$reason" "$PLAYER_EXITS" "$LAST_EXIT" <<'PY'
import json, os, sys, tempfile, time
from pathlib import Path
path = Path(sys.argv[1])
data = dict(schema=1, observed_at=time.time(), worker_pid=int(sys.argv[2]),
            player_pid=int(sys.argv[3]), state=sys.argv[4], reason=sys.argv[5],
            player_exits=int(sys.argv[6]), last_exit=int(sys.argv[7]) if sys.argv[7] else None)
tmp = None
try:
    fd, tmp = tempfile.mkstemp(prefix=".bgm-status-", dir=path.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, separators=(",", ":"))
        stream.write("\n")
    os.replace(tmp, path)
except OSError:
    pass  # Telemetry cannot change the playback or lifecycle policy.
finally:
    if tmp and os.path.exists(tmp):
        os.unlink(tmp)
PY
}

player_running() {
	[ -n "$PLAYER_PID" ] || return 1
	local child
	# The shell's own job table, not a name match or an arbitrary saved PID.
	for child in $(jobs -p); do
		if [ "$child" = "$PLAYER_PID" ] && kill -0 "$child" 2>/dev/null; then
			return 0
		fi
	done
	return 1
}

stop_player() {
	local attempt
	if player_running; then
		kill -TERM "$PLAYER_PID" 2>/dev/null || true
		for ((attempt=0; attempt<20; attempt++)); do
			player_running || break
			sleep 0.1
		done
		if player_running; then kill -KILL "$PLAYER_PID" 2>/dev/null || true; fi
	fi
	if [ -n "$PLAYER_PID" ]; then wait "$PLAYER_PID" 2>/dev/null || true; fi
	PLAYER_PID=""
}

run_bgm_worker() {
	local reason state
	# Keep the same inode and let the owned player inherit the lock. Even an
	# abrupt worker death must not permit a second player beside its survivor.
	(umask 077; mkdir -p "$STATE_DIR") || return 1
	exec 9>>"$STATE_DIR/bgm_worker.lock" || return 1
	flock -n 9 || return 1
	trap 'stop_player; report_state stopped worker_stopped' EXIT
	trap 'exit 0' TERM INT
	while :; do
		if reason=$(cli_game_active); then
			state=running
			if ! player_running; then
				state=starting
				if [ -n "$PLAYER_PID" ]; then
					wait "$PLAYER_PID" 2>/dev/null; LAST_EXIT=$?
					PLAYER_EXITS=$((PLAYER_EXITS + 1))
					state=restarting
				fi
				# Isolate fallback mute/volume memory from other ffplay streams.
				# A fixed identity preserves an operator mute of this BGM itself;
				# never unmute the shared sink or delete the restore database.
				PULSE_PROP="application.id=docich.corner-bgm module-stream-restore.id=docich-corner-bgm" \
				PULSE_SERVER="$BGM_PULSE_SERVER" PULSE_SINK="$SINK" ffplay -nodisp -loop 0 -volume "$VOL" -window_title "$TAG" "$BGM_FILE" >/dev/null 2>&1 &
				PLAYER_PID=$!
			fi
		else
			stop_player
			state=blocked
		fi
		report_state "$state" "${reason:-policy_unavailable}"
		docich_poll_sleep 1
	done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	run_bgm_worker
fi
