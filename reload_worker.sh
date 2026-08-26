#!/bin/bash
# reload_worker.sh - request a live worker to reload .env and eloop_lib.sh.
#
# Usage:
#   ./reload_worker.sh radio_worker
#   ./reload_worker.sh all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

target="${1:-}"
signal="${RELOAD_SIGNAL:-HUP}"

workers=(chat_worker youtube_worker kick_worker audio_worker radio_worker prediction_worker)

usage() {
	echo "Usage: $0 {all|chat_worker|youtube_worker|kick_worker|audio_worker|radio_worker|prediction_worker}" >&2
}

reload_one() {
	local worker="$1"
	local pid_file="tmp/state/${worker}.pid"
	local pid=""
	local waited=0

	if [ ! -f "$pid_file" ]; then
		echo "${worker}: no pid file (${pid_file})" >&2
		return 1
	fi
	pid=$(cat "$pid_file" 2>/dev/null || true)
	case "$pid" in
	''|*[!0-9]*)
		echo "${worker}: invalid pid (${pid:-empty})" >&2
		return 1
		;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		echo "${worker}: not running (pid=${pid})" >&2
		return 1
	fi

	kill "-${signal}" "$pid"
	while [ "$waited" -lt 5 ]; do
		sleep 1
		waited=$((waited + 1))
		if ! kill -0 "$pid" 2>/dev/null; then
			echo "${worker}: reload signal stopped the worker (pid=${pid}, signal=${signal}); start it again to pick up reload-capable code" >&2
			return 1
		fi
	done
	echo "${worker}: reload requested (pid=${pid}, signal=${signal})"
}

case "$target" in
all)
	rc=0
	for worker in "${workers[@]}"; do
		reload_one "$worker" || rc=1
	done
	exit "$rc"
	;;
chat_worker|youtube_worker|kick_worker|audio_worker|radio_worker|prediction_worker)
	reload_one "$target"
	;;
*)
	usage
	exit 2
	;;
esac
