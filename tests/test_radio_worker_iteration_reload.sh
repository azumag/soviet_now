#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

awk '
  /^_run_iteration\(\)/ {copy=1}
  /^# --- pause gate/ {copy=0}
  copy {print}
' "$ROOT/workers/radio_worker.sh" >"$TMP/iteration.sh"
# shellcheck disable=SC1090
source "$TMP/iteration.sh"

OUT="$TMP/out"
printf '2\n' >"$TMP/game_count"
printf '%s\n' "$(date +%s)" >"$TMP/last_scheduler"
cat >"$TMP/eloop_lib.sh" <<'LIB'
schedule_nonessential_audio_jobs() { printf 'new\n' >>"$OUT"; }
LIB

GAME_COUNT_FILE="$TMP/game_count"
_LAST_GAME_NUM=1
_LAST_SCHEDULER_RUN_FILE="$TMP/last_scheduler"
_SCHEDULER_INTERVAL_SEC=999999
POLL_INTERVAL=0
AI_STDERR_LOG="$TMP/ai.stderr"
_log() { :; }
_last_score() { printf '0\n'; }
schedule_nonessential_audio_jobs() { printf 'old\n' >>"$OUT"; }
process_external_audio_triggers() { :; }
sleep() { :; }

cd "$TMP"
_run_iteration

[ "$(cat "$OUT")" = "new" ] || {
  echo "FAIL: iteration reload did not update parent-shell functions: $(cat "$OUT" 2>/dev/null || echo empty)" >&2
  exit 1
}

echo "PASS: iteration reload persists sourced functions in the worker shell"
