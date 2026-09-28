#!/usr/bin/env bash
# Execute one command while holding the shared OpenCode DB rotation gate.
# This wrapper lets non-Bash callers (notably soren91/text_ai.mjs) participate
# in the same flock contract as the shell AI paths without shell interpolation.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ELOOP_LIB_DIR="${ELOOP_LIB_DIR:-$ROOT}"
# shellcheck source=opencode_db_retention.sh
source "$ROOT/lib/opencode_db_retention.sh"

_opencode_rotation_gate_run "$@"
