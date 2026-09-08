#!/usr/bin/env bash
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
TMP_STATE_DIR=.
STRATEGY_FILE=strategy.py
IMPROVE_LOCK_FILE=lock.json
CURRENT_RUN_AUTO_REPAIR_ENABLED=0
AB_ARM=A
touch "$STRATEGY_FILE"
source "$ROOT/strategy/improve.sh"
python3() { echo A; }
_ab_active() { return 0; }
_ab_is_arm_hash() { return 0; }
_has_active_branch() { return 0; }
update_rolling_scores() { touch rolling_recorded; }
_update_current_strategy_run() { touch current_recorded; }
accumulate_game_data() { touch accumulated; }
echo '{"count":48,"files":["first.jsonl"]}' > "$IMPROVE_LOCK_FILE"
cp "$IMPROVE_LOCK_FILE" expected.json
record_completed_game_for_adaptive_improvement later.jsonl 100 false
[ ! -e accumulated ]
[ -e rolling_recorded ] && [ -e current_recorded ]
cmp "$IMPROVE_LOCK_FILE" expected.json
rm "$IMPROVE_LOCK_FILE"
record_completed_game_for_adaptive_improvement later.jsonl 100 false
[ -e accumulated ]
rm accumulated
AB_ARM=B
record_completed_game_for_adaptive_improvement later.jsonl 100 false
[ ! -e accumulated ]
echo 'PASS: reserved AB batch frozen; rolling/current histories continue; accumulation resumes without lock'
