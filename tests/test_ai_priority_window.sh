#!/usr/bin/env bash
# Offline boundary/dispatch integration. No real provider/credentials are read.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
export ELOOP_LIB_DIR="$ROOT" AI_BACKOFF_DIR="$TMP/backoff" AI_FAIL_STREAK_DIR="$TMP/streak"
export AI_FAILURE_BACKOFF_DIR="$TMP/failure" AI_FAMILY_BACKOFF_DIR="$TMP/family"
export AI_GENERATION_QUEUE_ENABLED=0 OPENCODE_ROTATION_GATE_ENABLED=0 OPENCODE_ABORT_RETRY=0
source "$ROOT/lib/ai_generate.sh"
source "$ROOT/core/helpers.sh"
log() { :; }
_ai_stats_record() { printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "${4:-}" >>"$TMP/stats"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
eq() { [ "$1" = "$2" ] || fail "$3: expected <$2> got <$1>"; }
GO=opencode-go:union-alpha
OR=openrouter:stealth/union-alpha
PREFIX="$GO,$OR"

AI_PRIORITY_NOW_EPOCH=1789649999
eq "$(_ai_priority_prepend 'codex:a,local')" 'codex:a,local' before
AI_PRIORITY_NOW_EPOCH=1789650000
eq "$(_ai_priority_prepend 'codex:a,local')" "$PREFIX,codex:a,local" start
AI_PRIORITY_NOW_EPOCH=1790254799
eq "$(_ai_priority_prepend "$OR,codex:a,$GO,$OR,local")" "$PREFIX,codex:a,local" dedup
AI_PRIORITY_NOW_EPOCH=1790254800
eq "$(_ai_priority_prepend 'codex:a,local')" 'codex:a,local' end
source "$ROOT/lib/ai_priority_window.sh"
eq "$(_ai_priority_prepend 'codex:a,local')" 'codex:a,local' reload
AI_PRIORITY_NOW_EPOCH=1789650000
eq "$(_ai_priority_prepend ', ,')" ', ,' empty
_is_peak_hours() { return 0; }
PEAK_HOURS_PRIORITY_AGENTS='local,codex:b'
# Caller sorting/last-winner ordering is preserved underneath the prefix.
ordered=$(_peak_priority_agent_list 'codex:a,local,codex:b')
eq "$(_ai_priority_prepend "$ordered")" "$PREFIX,$ordered" peak
ordered='codex:last,local,codex:a'
eq "$(_ai_priority_prepend "$ordered")" "$PREFIX,$ordered" last-winner

# Real _ai_dispatch + backend selection; only external CLIs are mocked.
export OPENCODE_BIN="$TMP/opencode" CALLS="$TMP/calls"
cat >"$OPENCODE_BIN" <<'SH'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = --model ]; then model="$2"; break; fi
  shift
done
printf '%s\n' "$model" >>"$CALLS"
printf '429 too many requests\n' >&2
exit 1
SH
chmod +x "$OPENCODE_BIN"
_contains_provider_error_text() { return 1; }
_ai_call_codex() { printf '%s\n' "$2" >>"$CALLS"; [ "$2" = codex:b ] && printf 'OK'; }
expected=$'opencode-go/union-alpha\nopenrouter/stealth/union-alpha\ncodex:a\ncodex:b'
for policy in base scoped; do
  [ "$policy" != scoped ] || source "$ROOT/lib/ai_generate_policy.sh"
  rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
  : >"$CALLS"
  printf 'fixture prompt' >prompt
  out=$(ai_generate_list COMMENT prompt codex:a,codex:b)
  eq "$out" OK "$policy output"
  eq "$(cat "$CALLS")" "$expected" "$policy fallback-order"
  [ -f "$AI_BACKOFF_DIR/opencode-go_union-alpha" ] || fail 'Go backoff missing'
  [ -f "$AI_BACKOFF_DIR/openrouter_stealth_union-alpha" ] || fail 'OR backoff missing'
  rm "$AI_BACKOFF_DIR/openrouter_stealth_union-alpha"
  _ai_backoff_check "$OR" || fail 'OR should be independent'
  if _ai_backoff_check "$GO"; then fail 'Go should remain backed off'; fi
  rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
  : >"$CALLS"
  eq "$(ai_generate COMMENT prompt codex:a codex:b)" OK "$policy two-agent"
  eq "$(cat "$CALLS")" "$expected" "$policy two-agent order"
done

# Time advances inside queue acquisition: zero CLI attempts, no false backoff.
source "$ROOT/lib/ai_generate.sh"
_ai_call_codex() { printf '%s\n' "$2" >>"$CALLS"; [ "$2" = codex:b ] && printf 'OK'; }
_ai_stats_record() { printf '%s\n' "$1" >>"$TMP/stats"; }
_ai_priority_now() { cat "$TMP/clock"; }
printf 1789650000 >"$TMP/clock"
_ai_generation_queue_enter() { printf 1790254800 >"$TMP/clock"; AI_GENERATION_QUEUE_LAST_TOKEN=fixture; }
_ai_generation_queue_leave() { printf released >>"$TMP/released"; }
AI_GENERATION_QUEUE_ENABLED=1
AI_RADIO_IMPROVE_GATE=0
rm -rf "$AI_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
: >"$CALLS"
eq "$(ai_generate_list COMMENT prompt codex:b)" OK queue-expiry
eq "$(cat "$CALLS")" codex:b queue-no-promoted-call
[ -s "$TMP/released" ] || fail 'queue not released'
[ ! -e "$AI_BACKOFF_DIR/opencode-go_union-alpha" ] || fail 'expired provider backoff'
grep -q priority_expired "$TMP/stats" || fail 'expiration telemetry'

# Retry must not dispatch after the boundary, but a completed response survives.
AI_GENERATION_QUEUE_ENABLED=0
OPENCODE_ABORT_RETRY=1
OPENCODE_ABORT_RETRY_WAIT_SEC=0
cat >"$OPENCODE_BIN" <<'SH'
#!/usr/bin/env bash
printf 'call\n' >>"$CALLS"
printf 1790254800 >"$CLOCK_FILE"
if [ "${SUCCESS:-0}" = 1 ]; then printf OK; exit 0; fi
exit 1
SH
export CLOCK_FILE="$TMP/clock"
printf 1790254799 >"$TMP/clock"
: >"$CALLS"
_AI_PRIORITY_CHAIN=1 _AI_PRIORITY_ORIGINAL_LIST=codex:b
_ai_call_opencode_unqueued TEST "$GO" prompt 5 >/dev/null
rc=$?
eq "$rc" 93 retry-expired
eq "$(cat "$CALLS")" call no-retry
printf 1790254799 >"$TMP/clock"
export SUCCESS=1
eq "$(_ai_call_opencode_unqueued TEST "$GO" prompt 5)" OK running-may-finish
unset SUCCESS _AI_PRIORITY_CHAIN _AI_PRIORITY_ORIGINAL_LIST

# Improve chain + retry + queue expiry use exactly the same scheduling contract.
source "$ROOT/strategy/ai.sh"
_ai_priority_now() { cat "$TMP/clock"; }
RUN_AI_IMPROVEMENT_MODE=1
RUN_AI_PRIMARY_RETRIES=3
build_prompt() { printf fixture; }
run_cmd() { printf '%s\n' "$1" >>"$CALLS"; [ "$1" = codex:b ]; }
rm -rf "$AI_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
printf 1789650000 >"$TMP/clock"
: >"$CALLS"
run_ai_list ANALYZE codex:a,codex:b prompt '' || fail improve-list
eq "$(cat "$CALLS")" "$GO"$'\n'"$OR"$'\ncodex:a\ncodex:b' improve-order
run_cmd() {
  _ai_priority_dispatch_allowed "$1" || return 93
  printf '%s\n' "$1" >>"$CALLS"
  if [ "$1" = "$GO" ]; then printf 1790254800 >"$TMP/clock"; return 1; fi
  [ "$1" = codex:b ]
}
rm -rf "$AI_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR"
printf 1790254799 >"$TMP/clock"
: >"$CALLS"
run_ai_list ANALYZE codex:b prompt '' || fail improve-expiry
eq "$(cat "$CALLS")" "$GO"$'\ncodex:b' improve-no-expired-OR

# Real run_cmd adapter (no auth inspection) rechecks after its CLI queue.
source "$ROOT/strategy/ai.sh"
_ai_priority_now() { cat "$TMP/clock"; }
RUN_AI_IMPROVEMENT_MODE=0
_opencode_run_lock_enter() { printf 1790254800 >"$TMP/clock"; OPENCODE_RUN_LOCK_LAST_TOKEN=fixture; }
_opencode_run_lock_leave() { printf released >>"$TMP/improve_released"; }
_AI_PRIORITY_CHAIN=1 _AI_PRIORITY_ORIGINAL_LIST=codex:b
printf 1790254799 >"$TMP/clock"
run_cmd "$OR" fixture >/dev/null
rc=$?
eq "$rc" 93 improve-post-queue
[ -s "$TMP/improve_released" ] || fail improve-lock-not-released
eq "$(_run_cmd_resolved_model "$OR")" openrouter/stealth/union-alpha improve-model
unset _AI_PRIORITY_CHAIN _AI_PRIORITY_ORIGINAL_LIST

# Direct fact-check chain without network or rewriting the original fallbacks.
source "$ROOT/broadcast/radio_factcheck.sh"
_radio_fetch_web_grounding() { :; }
_radio_compact_fact_check_context() { printf context; }
_radio_cleanup_fact_checked_text() { cat; }
_sanitize_onair_text() { cat; }
_normalize_radio_tone() { cat; }
_radio_extract_fact_check_issues() { :; }
_radio_fact_check_style_reason() { :; }
_is_valid_radio_talk() { [ "$1" = OK ]; }
_run_opencode_radio() { printf '%s\n' "$1" >>"$CALLS"; [ "$1" = codex:b ] && printf OK; }
RADIO_FACT_CHECK_AGENT=codex:a RADIO_FACT_CHECK_SECONDARY=codex:b
printf 1789650000 >"$TMP/clock"
: >"$CALLS"
eq "$(_radio_fact_check_body soviet context original)" OK factcheck
eq "$(cat "$CALLS")" "$GO"$'\n'"$OR"$'\ncodex:a\ncodex:b' factcheck-order
RADIO_FACT_CHECK_ENABLED=0
: >"$CALLS"
eq "$(_radio_fact_check_body soviet context original)" original disabled-factcheck
[ ! -s "$CALLS" ] || fail disabled-feature-called

echo 'PASS: fixed window, base/policy/two-agent dispatch, queues, retry, improve, fact-check'
