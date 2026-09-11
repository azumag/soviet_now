#!/usr/bin/env bash
set -euo pipefail

ROOT="$(mktemp -d)"
WORKER_PID=""
cleanup() {
	if [ -n "${WORKER_PID:-}" ]; then
		touch "$ROOT/tmp/stop" 2>/dev/null || true
		kill "$WORKER_PID" 2>/dev/null || true
		wait "$WORKER_PID" 2>/dev/null || true
	fi
	rm -rf "$ROOT"
}
trap cleanup EXIT

mkdir -p "$ROOT/workers" "$ROOT/lib" "$ROOT/tmp/state" "$ROOT/logs"
cp workers/radio_worker.sh "$ROOT/workers/radio_worker.sh"
cat >"$ROOT/lib/background_priority.sh" <<'EOF'
soren_background_priority() { :; }
EOF
cat >"$ROOT/.env" <<'EOF'
RADIO_WORKER_INTERVAL=1
RADIO_WORKER_SCHEDULER_INTERVAL=1
WORKER_PID_HEARTBEAT_INTERVAL=1
EOF
printf '0\n' >"$ROOT/game_count.txt"

write_runtime() {
	local version="$1"
	cat >"$ROOT/eloop_lib.sh" <<EOF
GAME_COUNT_FILE="\$PWD/game_count.txt"
AI_STDERR_LOG="\$PWD/logs/ai_stderr.log"
_last_score() { echo 0; }
schedule_nonessential_audio_jobs() {
	printf '%s\\n' '$version' >>"\$PWD/tmp/calls"
}
process_external_audio_triggers() { :; }
EOF
}

wait_for_call() {
	local expected="$1" i
	for i in $(seq 1 80); do
		if grep -qx "$expected" "$ROOT/tmp/calls" 2>/dev/null; then
			return 0
		fi
		sleep 0.1
	done
	echo "timed out waiting for runtime $expected" >&2
	cat "$ROOT/tmp/worker.log" >&2 2>/dev/null || true
	return 1
}

write_runtime v1
(
	cd "$ROOT"
	git init -q
	git config user.name test
	git config user.email test@example.invalid
	git add .
	git commit -qm v1
)

(
	cd "$ROOT"
	bash workers/radio_worker.sh >tmp/worker.log 2>&1
) &
WORKER_PID=$!
wait_for_call v1

write_runtime v2
(
	cd "$ROOT"
	git add eloop_lib.sh
	git commit -qm v2
)
wait_for_call v2

grep -q 'runtime refresh complete' "$ROOT/tmp/worker.log"

touch "$ROOT/tmp/stop"
wait "$WORKER_PID"
WORKER_PID=""

echo "radio worker runtime refresh test: PASS"
