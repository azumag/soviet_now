#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ATTEMPT_LOG="$TMP/attempts.log"
prompt="$TMP/prompt.txt"
printf 'test prompt\n' >"$prompt"
: >"$ATTEMPT_LOG"

log() { :; }

# Minimal versions of the two functions wrapped by ai_prepass_budget.sh. The
# fake chain preserves rc=93 as "not attempted", matching the real policy.
_ai_dispatch() {
	local label="$1" agent="$2" _prompt="$3" timeout_override="${4:-}"
	printf '%s|%s|%s|%s\n' "$label" "$agent" "$timeout_override" "${OPENCODE_ABORT_RETRY:-unset}" >>"$ATTEMPT_LOG"
	case "$agent" in
	slow)
		local delay="${timeout_override:-1}"
		[ "$delay" -gt 1 ] && delay=1
		sleep "$delay"
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
		if [ "$rc" -eq 93 ]; then
			continue
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ]; then
			printf '%s' "$output"
			return 0
		fi
	done
	return 1
}

source "$ROOT/lib/ai_prepass_budget.sh"

ok=0
fail=0
pass() { printf 'ok - %s\n' "$1"; ok=$((ok + 1)); }
fail_case() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail + 1)); }

# 1. Optional prepass uses one wall-clock budget. The first slow candidate gets
# the remaining budget; after it consumes the second, later candidates are not
# dispatched at all and therefore cannot extend lane ownership. Same-provider
# retry is disabled only inside the optional prepass so one backend cannot spend
# the remaining budget twice.
: >"$ATTEMPT_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=1
export OPENCODE_ABORT_RETRY=1
start=$(date +%s)
ai_generate_list 'RADIO:news:prepass' "$prompt" 'slow,success' >/dev/null 2>&1 || true
elapsed=$(( $(date +%s) - start ))
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
first_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
first_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ "$lines" -eq 1 ] && [ "$first_timeout" = 1 ] && [ "$first_retry" = 0 ] \
	&& [ "$elapsed" -le 2 ] && [ "$OPENCODE_ABORT_RETRY" = 1 ]; then
	pass 'optional prepass releases after total budget and restores retry policy'
else
	fail_case "optional prepass total budget (lines=$lines timeout=$first_timeout retry=$first_retry elapsed=$elapsed restored=${OPENCODE_ABORT_RETRY:-unset})"
fi

# 2. Required main generation is not constrained by the prepass budget shim and
# retains the normal OpenCode retry policy.
: >"$ATTEMPT_LOG"
main_out=$(ai_generate_list 'RADIO:news' "$prompt" 'success' 2>/dev/null || true)
main_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
main_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ "$main_out" = ok ] && [ -z "$main_timeout" ] && [ "$main_retry" = 1 ]; then
	pass 'radio main timeout and retry policy are unchanged'
else
	fail_case "radio main policy unchanged (out=$main_out timeout=$main_timeout retry=$main_retry)"
fi

# 3. A caller-provided timeout shorter than the remaining total budget remains
# authoritative; the shim only tightens, never lengthens, per-dispatch limits.
: >"$ATTEMPT_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=5
prepass_out=$(ai_generate_list 'RADIO:weather:prepass' "$prompt" 'success' 2 2>/dev/null || true)
explicit_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
if [ "$prepass_out" = ok ] && [ "$explicit_timeout" = 2 ]; then
	pass 'explicit shorter prepass timeout is preserved'
else
	fail_case "explicit shorter timeout preserved (out=$prepass_out timeout=$explicit_timeout)"
fi

# 4. Misconfiguration cannot silently restore multi-minute optional ownership.
RADIO_PREPASS_TOTAL_BUDGET_SEC=garbage
invalid=$(_ai_prepass_total_budget_sec)
RADIO_PREPASS_TOTAL_BUDGET_SEC=99999
capped=$(_ai_prepass_total_budget_sec)
if [ "$invalid" = 60 ] && [ "$capped" = 120 ]; then
	pass 'prepass budget defaults safely and is capped at two minutes'
else
	fail_case "prepass budget validation (invalid=$invalid capped=$capped)"
fi

# 5. Runtime shim must load the budget wrapper after the policy implementation.
policy_line=$(grep -nF 'source "$ELOOP_LIB_DIR/lib/ai_generate_policy.sh"' "$ROOT/eloop_lib.sh" | cut -d: -f1)
budget_line=$(grep -nF 'source "$ELOOP_LIB_DIR/lib/ai_prepass_budget.sh"' "$ROOT/eloop_lib.sh" | cut -d: -f1)
if [ -n "$policy_line" ] && [ -n "$budget_line" ] && [ "$budget_line" -gt "$policy_line" ]; then
	pass 'runtime loads prepass budget after ai_generate policy'
else
	fail_case 'runtime load order keeps prepass budget after ai_generate policy'
fi

printf '# pass=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
