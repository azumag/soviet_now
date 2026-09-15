#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/start_shared_overlay_service.sh"
UNIT="$ROOT/deploy/soren-shared-overlay/soren-shared-overlay.service"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# A pidfile surviving an old/crashed invocation is not proof of process
# identity.  Its numeric PID may have been reused, so startup must never signal
# a process merely because its PID appeared in that stale file.
if grep -Eq 'while[[:space:]]+read[[:space:]]+-r[[:space:]]+p|kill[[:space:]]+-9[[:space:]]+"\$p"' "$SCRIPT"; then
  fail "startup must not kill PIDs read from a previous pidfile"
fi

grep -Fq 'rm -f "$PIDFILE"' "$SCRIPT" \
  || fail "stale pidfile metadata must be discarded without signaling old PIDs"

grep -Fq 'KillMode=control-group' "$UNIT" \
  || fail "systemd must own cleanup of all service child processes"

grep -Fq 'Restart=always' "$UNIT" \
  || fail "service restart contract unexpectedly changed"

echo "ALL PASS"
