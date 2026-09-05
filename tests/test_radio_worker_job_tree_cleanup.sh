#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Load only the two process-tree helpers from the production worker.
awk '
  /^_job_tree_pids\(\)/ {copy=1}
  /^_cleanup\(\)/ {copy=0}
  copy {print}
' "$ROOT/workers/radio_worker.sh" >"$TMP/helpers.sh"
# shellcheck disable=SC1090
source "$TMP/helpers.sh"

jobs() { printf '%s\n' 100 999; }
pgrep() {
  [ "${1:-}" = "-P" ] || return 1
  case "${2:-}" in
    100) printf '%s\n' 200 201 ;;
    200) printf '%s\n' 300 ;;
  esac
}
kill() {
  printf '%s %s\n' "${1:-}" "${2:-}" >>"$TMP/kills"
  return 0
}
sleep() { :; }
_HEARTBEAT_PID=999

_terminate_background_jobs 0
terminated="${_TERMINATED_BACKGROUND_JOB_COUNT:-0}"

[ "${terminated:-0}" = 4 ] || { echo "FAIL: expected 4 terminated processes, got ${terminated:-empty}" >&2; exit 1; }
[ "$(grep -c '^-TERM ' "$TMP/kills")" = 4 ] || { echo "FAIL: TERM count" >&2; exit 1; }
[ "$(grep -c '^-KILL ' "$TMP/kills")" = 4 ] || { echo "FAIL: KILL count" >&2; exit 1; }
grep -q ' 999$' "$TMP/kills" && { echo "FAIL: heartbeat was terminated during reload cleanup" >&2; exit 1; }
[ "$(sed -n '1,4p' "$TMP/kills" | awk '{print $2}' | paste -sd, -)" = "300,200,201,100" ] || {
  echo "FAIL: descendants were not terminated before their parent" >&2
  exit 1
}

echo "PASS: nested background tree stopped and heartbeat preserved"
