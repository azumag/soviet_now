#!/usr/bin/env bash
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
mkdir -p tmp/state
TMP_STATE_DIR=tmp/state
IMPROVE_LOCK_FILE=tmp/improve.lock
AB_STATE_FILE=tmp/state/ab_state.json
AB_CANDIDATE_DIR=tmp/state/ab_candidate
source "$ROOT/strategy/improve.sh"
log() { :; }
reload_runtime_toggles() { :; }
_main_strategy_runner_active_for_improve() { touch reached_runner; return 0; }
echo '{"count":48,"hash":"A"}' > "$IMPROVE_LOCK_FILE"
echo '{}' > "$AB_STATE_FILE"
trigger_adaptive_improvement
[ ! -e reached_runner ] || { echo 'FAIL: AB did not defer trigger'; exit 1; }
grep -q '48' "$IMPROVE_LOCK_FILE"
rm "$AB_STATE_FILE"
mkdir -p "$AB_CANDIDATE_DIR"
trigger_adaptive_improvement
[ ! -e reached_runner ] || { echo 'FAIL: pending candidate did not defer trigger'; exit 1; }
rmdir "$AB_CANDIDATE_DIR"
trigger_adaptive_improvement
[ -e reached_runner ] || { echo 'FAIL: no resume after AB'; exit 1; }

# A state appearing during spawn mutex acquisition must also block the worker.
_acquire_spawn_lock() { touch "$AB_STATE_FILE"; }
_release_spawn_lock() { touch released; }
_improve_spawn_state_blocks_start() { touch reached_worker_check; return 0; }
if _start_improvement_job '' '' false 48 normal; then
    echo 'FAIL: blocked spawn reported success'; exit 1
fi
[ -e released ] && [ ! -e reached_worker_check ] || { echo 'FAIL: post-mutex AB check missing'; exit 1; }
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
_ab_start_from_bundle_locked() { touch reached_ab_start; }
_acquire_spawn_lock() { return 0; }
_improve_spawn_state_blocks_start() { return 0; }
rc=0
_ab_start_from_bundle ignored || rc=$?
[ "$rc" = 75 ] && [ ! -e reached_ab_start ] || { echo 'FAIL: AB raced a running worker'; exit 1; }
_improve_spawn_state_blocks_start() { return 1; }
_ab_start_from_bundle ignored
[ -e reached_ab_start ] || { echo 'FAIL: AB did not resume after worker'; exit 1; }
echo 'PASS: AB/pending defer, lock retained, resume, post-mutex recheck'
