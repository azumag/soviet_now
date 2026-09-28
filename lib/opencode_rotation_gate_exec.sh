#!/usr/bin/env bash
# Execute one command while holding the shared OpenCode DB rotation gate.
#
# This wrapper is for callers that cannot source the Bash helper directly
# (notably soren91/text_ai.mjs and probe_free_slot.sh).  Use GNU flock's
# --no-fork mode so the wrapper is replaced by the real command after the
# shared lock is acquired.  Timeouts/signals therefore reach the OpenCode
# process itself instead of leaving an orphan writer behind.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ELOOP_LIB_DIR="${ELOOP_LIB_DIR:-$ROOT}"
# shellcheck source=opencode_db_retention.sh
source "$ROOT/lib/opencode_db_retention.sh"

case "${OPENCODE_ROTATION_GATE_ENABLED:-1}" in
1) ;;
*) exec "$@" ;;
esac

if ! command -v flock >/dev/null 2>&1; then
	log "[OPENCODE:gate] flock unavailable; running ungated" >&2
	exec "$@"
fi

gate="$(_opencode_rotation_gate_path)"
wait_sec="${OPENCODE_ROTATION_GATE_WAIT_SEC:-120}"
case "$wait_sec" in '' | *[!0-9]*) wait_sec=120 ;; esac
[ "$wait_sec" -lt 1 ] && wait_sec=1
mkdir -p "$(dirname "$gate")" 2>/dev/null || true
if ! : >>"$gate" 2>/dev/null; then
	log "[OPENCODE:gate] cannot open gate; running ungated" >&2
	exec "$@"
fi

# -F/--no-fork keeps one PID from this wrapper through the real command.
# -E 124 preserves the existing fail-closed timeout contract.
exec flock -s -w "$wait_sec" -E 124 --no-fork "$gate" "$@"
