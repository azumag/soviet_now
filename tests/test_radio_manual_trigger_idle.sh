#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

sed -n '/^process_external_audio_triggers() {/,/^}/p' "$ROOT/broadcast/scheduler.sh" >"$TMP/function.sh"
# shellcheck disable=SC1090
source "$TMP/function.sh"

MANUAL_AUDIO_TRIGGER_DIR="$TMP/queue"
MANUAL_AUDIO_TRIGGER_MAX_PER_TICK=2
GAME_COUNT_FILE="$TMP/game_count.txt"
mkdir -p "$MANUAL_AUDIO_TRIGGER_DIR"
printf '42\n' >"$GAME_COUNT_FILE"

# These were on the old idle path. Any call is a regression.
ls() { printf 'ls\n' >>"$TMP/legacy-exec"; return 91; }
sort() { printf 'sort\n' >>"$TMP/legacy-exec"; return 92; }
head() { printf 'head\n' >>"$TMP/legacy-exec"; return 93; }

_last_score() {
  printf 'score\n' >>"$TMP/score-calls"
  printf '77\n'
}
_dispatch_manual_audio_trigger() {
  printf '%s|%s|%s\n' "${1##*/}" "$2" "$3" >>"$TMP/dispatched"
}
log() { :; }

# Empty queue is the common case: no legacy listing pipeline and no score read.
process_external_audio_triggers "42" ""
[ ! -e "$TMP/legacy-exec" ]
[ ! -e "$TMP/score-calls" ]
[ ! -e "$TMP/dispatched" ]

# Preserve sorted filename order and max-per-tick semantics when work exists.
printf 'c\n' >"$MANUAL_AUDIO_TRIGGER_DIR/c.cmd"
printf 'a\n' >"$MANUAL_AUDIO_TRIGGER_DIR/a.cmd"
printf 'b\n' >"$MANUAL_AUDIO_TRIGGER_DIR/b.cmd"
process_external_audio_triggers "42" ""

cat >"$TMP/expected" <<'EOF'
a.processing|42|77
b.processing|42|77
EOF
cmp "$TMP/expected" "$TMP/dispatched"
[ "$(wc -l <"$TMP/score-calls" | tr -d ' ')" = 1 ]
[ -f "$MANUAL_AUDIO_TRIGGER_DIR/c.cmd" ]
[ ! -e "$TMP/legacy-exec" ]

echo "radio manual trigger idle test: PASS"
