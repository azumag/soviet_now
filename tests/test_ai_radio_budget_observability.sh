#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
ATTEMPT_LOG="$TMP/attempts.log"
STATS_LOG="$TMP/stats.log"
prompt="$TMP/prompt.txt"
printf 'test prompt\n' >"$prompt"

log() { :; }
_ai_stats_record() {
	printf '%s|%s\n' "$1" "$2" >>"$STATS_LOG"
}

# Minimal unwrapped generation contract. Only provider calls that actually execute
# are written to ATTEMPT_LOG; this lets the test prove the later candidate was
# skipped by the total budget rather than failing at the provider.
_ai_dispatch() {
	local _label="$1" agent="$2" _prompt="$3" timeout_override="${4:-}"
	printf '%s|%s\n' "$agent" "$timeout_override" >>"$ATTEMPT_LOG"
	case "$agent" in
	slow)
		sleep "${timeout_override:-1}"
		return 1
		;;
	success)
		printf 'ok'
		return 0
		;;
	*) return 1 ;;
	esac
}

ai_generate_list() {
	local label="$1" prompt_file="$2" raw="$3" timeout_override="${4:-}"
	local agent output rc
	local agents=()
	IFS=',' read -ra agents <<<"$raw"
	for agent in "${agents[@]}"; do
		if output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override"); then
			rc=0
		else
			rc=$?
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ]; then
			printf '%s' "$output"
			return 0
		fi
	done
	return 1
}

source "$ROOT/lib/ai_prepass_budget.sh"

fail() { printf 'not ok - %s\n' "$1" >&2; exit 1; }
pass() { printf 'ok - %s\n' "$1"; }

# Optional prepass: one slow executed candidate consumes the chain budget. The
# downstream success candidate is not dispatched, and exactly one fixed event is
# recorded for the chain.
: >"$ATTEMPT_LOG"
: >"$STATS_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=1
prepass_out=$(ai_generate_list 'RADIO:news:prepass' "$prompt" 'slow,success' 2>/dev/null || true)
[ -z "$prepass_out" ] || fail 'prepass unexpectedly produced output'
[ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ] || fail 'prepass dispatched after deadline'
[ "$(grep -c '^budget_exhausted|RADIO:news:prepass$' "$STATS_LOG" || true)" -eq 1 ] || fail 'prepass budget event missing or duplicated'
pass 'prepass records one budget-exhausted event without dispatching remaining candidate'

# Live main uses the same observation while preserving its existing validator gate.
: >"$ATTEMPT_LOG"
: >"$STATS_LOG"
RADIO_MAIN_TOTAL_BUDGET_SEC=1
main_out=$(ai_generate_list 'RADIO:news' "$prompt" 'slow,success' '' '_radio_is_valid_generation_candidate' 2>/dev/null || true)
[ -z "$main_out" ] || fail 'main unexpectedly produced output'
[ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ] || fail 'main dispatched after deadline'
[ "$(grep -c '^budget_exhausted|RADIO:news$' "$STATS_LOG" || true)" -eq 1 ] || fail 'main budget event missing or duplicated'
pass 'live main records one budget-exhausted event without dispatching remaining candidate'

# Healthy and non-live RADIO calls must not generate the new event.
: >"$ATTEMPT_LOG"
: >"$STATS_LOG"
unset RADIO_MAIN_TOTAL_BUDGET_SEC
healthy=$(ai_generate_list 'RADIO:weather' "$prompt" 'success' '' '_radio_is_valid_generation_candidate' 2>/dev/null || true)
[ "$healthy" = ok ] || fail 'healthy main did not succeed'
[ "$(grep -c '^budget_exhausted|' "$STATS_LOG" || true)" -eq 0 ] || fail 'healthy main emitted budget event'

batch=$(ai_generate_list 'RADIO:batch_commentary' "$prompt" 'success' 2>/dev/null || true)
[ "$batch" = ok ] || fail 'non-live RADIO did not succeed'
[ "$(grep -c '^budget_exhausted|' "$STATS_LOG" || true)" -eq 0 ] || fail 'non-live RADIO emitted budget event'
pass 'healthy and non-live RADIO calls do not emit budget-exhausted events'

# Marker files are implementation-private and must be removed after every call.
if compgen -G "$TMP/soren-radio-budget.*" >/dev/null; then
	fail 'budget marker leaked after chain completion'
fi
pass 'budget marker is cleaned after chain completion'
