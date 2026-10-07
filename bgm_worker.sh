#!/bin/bash
# CLIゲーム向けの補助BGM。本編の停止が確認できない間は再生しない。
# canonicalに古いCLIゲーム名が残っても、SorenのBGMへ重ねない。
# DOCICH_CANONICAL / SOREN_BGM_FILE / SOREN_BGM_SINK / SOREN_BGM_VOLUME は従来通り。
# SOREN_BGM_STATE_DIR は既存Soren tmp/stateを参照する（別のlifecycleは作らない）。
# systemd soren-bgm.service用。コード配備後はこのworkerだけの再起動が必要。

BGM_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${DOCICH_CANONICAL:-/home/ubuntu/docich/run-soren-live/game_switch.json}"
BGM_FILE="${SOREN_BGM_FILE:-/home/ubuntu/soren/sorengame/assets/BGM/インターナショナル.ogg}"
SINK="${SOREN_BGM_SINK:-soren_null}"
VOL="${SOREN_BGM_VOLUME:-25}"
STATE_DIR="${SOREN_BGM_STATE_DIR:-$BGM_SCRIPT_DIR/tmp/state}"
source "$BGM_SCRIPT_DIR/lib/poll_wait.sh" 2>/dev/null || docich_poll_sleep() { sleep "${1:-1}" 2>/dev/null || true; }
TAG="soren-bgm-loop"
PLAYER_PID=""

cli_game_active() {
	[ -f "$BGM_FILE" ] || return 1
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
    raw = [read_file(path) for path in paths]
    canonical, request, ack = [json.loads(data) for data in raw[:3]]
    if not all(isinstance(item, dict) for item in (canonical, request, ack)):
        raise ValueError("invalid state")
    active = canonical.get("active")
    game = active.get("game") if isinstance(active, dict) else None
    if (canonical.get("phase") != "ready" or not isinstance(game, str) or not game
            or game in {"sorengame", "soren91", "hanjuku-hero"}):
        raise ValueError("not a fallback game")
    # A matching terminal stop is positive proof; a CLI name or pause marker
    # alone is not. Terminal stopped acknowledgements intentionally have no TTL.
    if (type(request.get("schema")) is not int or request["schema"] != 1
            or type(ack.get("schema")) is not int or ack["schema"] != 1
            or request.get("game") != "sorengame" or ack.get("status") != "stopped"):
        raise ValueError("native stop not confirmed")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
        raise ValueError("invalid request")
    if type(request.get("generation")) is not int or request["generation"] < 1:
        raise ValueError("invalid generation")
    deadline = request.get("deadline_epoch")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("invalid deadline")
    if not isinstance(request.get("deadline_at"), str) or not request["deadline_at"]:
        raise ValueError("invalid deadline")
    ack_deadline = ack.get("deadline_epoch")
    if (type(ack_deadline) not in (int, float) or not math.isfinite(ack_deadline)
            or ack_deadline != deadline):
        raise ValueError("stale acknowledgement")
    for field in ("request_id", "game", "generation", "deadline_at"):
        if type(ack.get(field)) is not type(request[field]) or ack[field] != request[field]:
            raise ValueError("stale acknowledgement")
    # Reject a handover that changed during this read. No marker, receipt,
    # canonical state, game process or shared audio resource is changed here.
    if any(read_file(path) != data for path, data in zip(paths, raw)):
        raise ValueError("handover changed")
except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
    raise SystemExit(1)
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
	# Keep the same inode and let the owned player inherit the lock. Even an
	# abrupt worker death must not permit a second player beside its survivor.
	(umask 077; mkdir -p "$STATE_DIR") || return 1
	exec 9>>"$STATE_DIR/bgm_worker.lock" || return 1
	flock -n 9 || return 1
	trap stop_player EXIT
	trap 'exit 0' TERM INT
	while :; do
		if cli_game_active; then
			if ! player_running; then
				stop_player
				PULSE_SINK="$SINK" ffplay -nodisp -loop 0 -volume "$VOL" -window_title "$TAG" "$BGM_FILE" >/dev/null 2>&1 &
				PLAYER_PID=$!
			fi
		else
			stop_player
		fi
		docich_poll_sleep 1
	done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	run_bgm_worker
fi
