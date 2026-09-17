#!/usr/bin/env bash
# Offline regression: configuration, not a clock/provider promotion, owns order.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
log() { :; }
export AI_BACKOFF_DIR="$TMP/backoff" AI_FAILURE_BACKOFF_DIR="$TMP/failure"
export AI_FAIL_STREAK_DIR="$TMP/streak" AI_STATS_DIR="$TMP/stats"
source "$ROOT/lib/ai_generate.sh"
GO=opencode-go:union-alpha
OR=openrouter:stealth/union-alpha
CHAIN="$GO,$OR,codex:fixture"
fail() { echo "FAIL: $*" >&2; exit 1; }
eq() { [ "$1" = "$2" ] || fail "$3: got [$1], expected [$2]"; }
printf 'fixture prompt\n' >"$TMP/prompt"
_ai_dispatch() {
  printf '%s\n' "$2" >>"$TMP/calls"
  [ "$2" != "$GO" ] || return "${GO_RC:-1}"
  [ "$2" != "$OR" ] || return 1
  printf 'ok'
}
# Exercise both definitions of the chain executor; the policy one is loaded last
# in production and owns scoped transient-failure cooldowns.
for policy in base scoped; do
  [ "$policy" != scoped ] || source "$ROOT/lib/ai_generate_policy.sh"
  for epoch in 1789649999 1789650000 1790254800; do
    export AI_PRIORITY_NOW_EPOCH="$epoch"
    rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
    : >"$TMP/calls"
    ai_generate_list TEST "$TMP/prompt" "$CHAIN" >"$TMP/output" || fail 'chain failed'
    eq "$(cat "$TMP/calls")" "$(printf '%s\n' "$GO" "$OR" codex:fixture)" "$policy explicit order/$epoch"
    eq "$(cat "$TMP/output")" ok output
    : >"$TMP/calls"
    ai_generate_list TEST "$TMP/prompt" "$CHAIN" >/dev/null || fail 'chain failed'
    eq "$(cat "$TMP/calls")" codex:fixture "$policy failure cooldown"
    : >"$TMP/calls"
    ai_generate_list TEST "$TMP/prompt" codex:fixture >/dev/null
    eq "$(cat "$TMP/calls")" codex:fixture "$policy no injected models"
  done
  rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
  GO_RC=79
  ai_generate_list TEST "$TMP/prompt" "$CHAIN" >/dev/null || fail 'chain failed'
  [ -f "$AI_BACKOFF_DIR/opencode-go_union-alpha" ] || fail 'rate limit backoff missing'
  # The shared 429 backoff must also gate direct-chain dispatch (radio/comment).
  (
    source "$ROOT/broadcast/radio_engine.sh"
    _ai_backoff_check() { [ "$1" != "$OR" ]; }
    _ai_backoff_remaining() { printf '%s\n' 300; }
    _radio_opencode_should_defer_for_improve() { return 1; }
    _run_opencode_radio_unqueued() { printf '%s\n' "$2" >>"$TMP/calls"; }
    _ai_generation_queue_run() { shift; "$@"; }
    _ai_stats_record() { :; }
    : >"$TMP/calls"
    rc=0
    _run_opencode_radio "$OR" "$TMP/prompt" >/dev/null || rc=$?
    eq "$rc" 1 'radio shared backoff gate'
    eq "$(cat "$TMP/calls")" '' 'radio queued call suppressed'
  )
  unset GO_RC
  for terminal in 91 92; do
    (
      _ai_dispatch() { printf '%s\n' "$2" >>"$TMP/calls"; return "$terminal"; }
      : >"$TMP/calls"
      rc=0
      ai_generate_list TEST "$TMP/prompt" 'codex:one,codex:two' '' '' '' "$TMP/kind" >/dev/null || rc=$?
      eq "$rc" "$terminal" 'terminal rc'
      eq "$(cat "$TMP/calls")" codex:one 'terminal stops chain'
    )
  done
done
# Single-primary API must not reinterpret a comma string as a chain.
: >"$TMP/calls"
ai_generate TEST "$TMP/prompt" 'codex:one,codex:two' '' >/dev/null
eq "$(cat "$TMP/calls")" 'codex:one,codex:two' 'single-agent API'
eq "$(_ai_resolved_model_from_agent "$OR")" openrouter/stealth/union-alpha 'OpenRouter model'
# Auxiliary translation: the explicit 4-candidate budget must reach existing
# candidates after both promoted routes fail; custom limits stay respected.
source "$ROOT/broadcast/comment.sh"
(
  _ai_backoff_check() { return 0; }
  _ai_dispatch() { printf '%s\n' "$2" >>"$TMP/calls"; return 1; }
  # Explicitly request the legacy cap; this fixture does not source config.sh.
  : >"$TMP/calls"
  COMMENT_TRANSLATION_MAX_ATTEMPTS=2 _comment_generate_translation "$TMP/prompt" "$CHAIN" 1 >/dev/null && fail 'translation unexpectedly succeeded'
  eq "$(cat "$TMP/calls")" "$(printf '%s\n' "$GO" "$OR")" 'translation explicit custom cap 2'
  : >"$TMP/calls"
  if COMMENT_TRANSLATION_MAX_ATTEMPTS=4 _comment_generate_translation "$TMP/prompt" "$CHAIN,codex:second,codex:third" 1 >/dev/null; then
    fail 'translation unexpectedly succeeded'
  fi
  eq "$(cat "$TMP/calls")" "$(printf '%s\n' "$GO" "$OR" codex:fixture codex:second)" 'translation cap 4 reaches two existing candidates, not full chain'
)
# Edit-contract classification accepts an explicit list without changing each
# run_cmd argument into a comma chain.
(
  run_cmd() {
    printf '%s\n' "$1" >>"$TMP/calls"
    [ "$1" != codex:fixture ] || printf '[]' >"$TMP/classification"
    return 1
  }
  _extract_comment_classification_json() { cat; }
  _validate_comment_classification_json() { grep -q '^\[\]$'; }
  _normalize_comment_classification_json() { cat; }
  : >"$TMP/calls"
  COMMENT_CLASSIFIER_EDIT_AGENTS="$CHAIN" _classify_comments_with_edit_contract "$TMP/prompt" "$TMP/classification" codex:old codex:fallback 1 >/dev/null || fail 'classifier failed'
  eq "$(cat "$TMP/calls")" "$(printf '%s\n' "$GO" "$OR" codex:fixture)" 'edit classifier chain'
)
# Priority helper removal must not leave load-time or dispatch-time dependencies.
if grep -En '_ai_priority_|_AI_PRIORITY_|_RUN_AI_SINGLE|priorityHandled|priorityOnly|prependPriority|priorityActive|priorityAgents|union-alpha' \
  "$ROOT/lib/ai_generate.sh" "$ROOT/lib/ai_generate_policy.sh" "$ROOT/strategy/ai.sh" \
  "$ROOT"/broadcast/*.sh "$ROOT/soren91/text_ai.mjs"; then
  fail 'runtime contains provider promotion logic'
fi
echo 'explicit chain contracts: PASS'
