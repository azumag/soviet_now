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
# attempt log records only candidates that actually reach the underlying dispatch.
_ai_dispatch() {
	local label="$1" agent="$2" _prompt="$3" timeout_override="${4:-}"
	printf '%s|%s|%s|%s\n' "$label" "$agent" "$timeout_override" "${OPENCODE_ABORT_RETRY:-unset}" >>"$ATTEMPT_LOG"
	case "$agent" in
	greedy)
		# Unlike ``slow`` (which caps its own sleep at 1s), a greedy candidate
		# really spends whatever timeout it was handed. This is the fixture for
		# azumag/docich#993 要件2: without a per-candidate cap it swallows the
		# whole remaining chain budget and the fallback never dispatches.
		local greedy_delay="${timeout_override:-1}"
		[[ "$greedy_delay" =~ ^[0-9]+$ ]] || greedy_delay=1
		[ "$greedy_delay" -lt 1 ] && greedy_delay=1
		sleep "$greedy_delay"
		return 1
		;;
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
# the remaining budget; later candidates never reach the provider dispatch. Retry
# policy is restored when the scoped chain returns.
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

# 2. Normal on-air main generation has its own larger total budget. A slow first
# candidate cannot spend the same budget twice via OpenCode retry, and candidates
# after the deadline are skipped before provider dispatch.
: >"$ATTEMPT_LOG"
RADIO_MAIN_TOTAL_BUDGET_SEC=1
export OPENCODE_ABORT_RETRY=1
start=$(date +%s)
main_timed_out=$(ai_generate_list 'RADIO:news' "$prompt" 'slow,success' '' '_radio_is_valid_generation_candidate' 2>/dev/null || true)
elapsed=$(( $(date +%s) - start ))
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
first_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
first_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ -z "$main_timed_out" ] && [ "$lines" -eq 1 ] && [ "$first_timeout" = 1 ] \
	&& [ "$first_retry" = 0 ] && [ "$elapsed" -le 2 ] && [ "$OPENCODE_ABORT_RETRY" = 1 ]; then
	pass 'live radio main releases after total budget without partial output'
else
	fail_case "live main total budget (out=$main_timed_out lines=$lines timeout=$first_timeout retry=$first_retry elapsed=$elapsed restored=${OPENCODE_ABORT_RETRY:-unset})"
fi

# 3. Healthy live main output still completes; the default chain budget is passed
# down as the provider timeout and retry policy is restored afterwards.
: >"$ATTEMPT_LOG"
unset RADIO_MAIN_TOTAL_BUDGET_SEC
main_out=$(ai_generate_list 'RADIO:weather' "$prompt" 'success' '' '_radio_is_valid_generation_candidate' 2>/dev/null || true)
main_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
main_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ "$main_out" = ok ] && [ "$main_timeout" -ge 179 ] && [ "$main_timeout" -le 180 ] \
	&& [ "$main_retry" = 0 ] && [ "$OPENCODE_ABORT_RETRY" = 1 ]; then
	pass 'healthy live radio main succeeds inside bounded chain'
else
	fail_case "healthy live main (out=$main_out timeout=$main_timeout retry=$main_retry restored=${OPENCODE_ABORT_RETRY:-unset})"
fi

# 4. Mixed-language local repair uses its own short total budget instead of the
# unbounded fallback path used by non-live RADIO consumers.
: >"$ATTEMPT_LOG"
RADIO_QUALITY_REPAIR_TOTAL_BUDGET_SEC=1
repair_out=$(ai_generate_list 'RADIO:news:mixed_repair' "$prompt" 'slow,success' '' \
	'_radio_is_valid_mixed_language_repair_candidate' 2>/dev/null || true)
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
repair_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
repair_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ -z "$repair_out" ] && [ "$lines" -eq 1 ] && [ "$repair_timeout" = 1 ] \
	&& [ "$repair_retry" = 0 ] && [ "$OPENCODE_ABORT_RETRY" = 1 ]; then
	pass 'mixed-language repair releases after its bounded total budget'
else
	fail_case "mixed repair total budget (out=$repair_out lines=$lines timeout=$repair_timeout retry=$repair_retry restored=${OPENCODE_ABORT_RETRY:-unset})"
fi

# 5. The repair caller shares one deadline across its per-span chains. An already
# expired shared deadline must not be replaced by a fresh budget for the next span.
: >"$ATTEMPT_LOG"
AI_RADIO_MIXED_REPAIR_TOTAL_DEADLINE_EPOCH=$(( $(date +%s) - 1 ))
expired_repair=$(ai_generate_list 'RADIO:news:mixed_repair' "$prompt" 'success' '' \
	'_radio_is_valid_mixed_language_repair_candidate' 2>/dev/null || true)
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
if [ -z "$expired_repair" ] && [ "$lines" -eq 0 ]; then
	pass 'mixed-language repair reuses one shared deadline across spans'
else
	fail_case "mixed repair shared deadline (out=$expired_repair lines=$lines)"
fi
unset AI_RADIO_MIXED_REPAIR_TOTAL_DEADLINE_EPOCH

# 6. RADIO users that are not the normal on-air main-generation contract remain
# untouched. This prevents a generic RADIO:* match from changing batch/poll policy.
: >"$ATTEMPT_LOG"
batch_out=$(ai_generate_list 'RADIO:batch_commentary' "$prompt" 'success' 2>/dev/null || true)
batch_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
batch_retry=$(awk -F'|' 'NR==1 {print $4}' "$ATTEMPT_LOG")
if [ "$batch_out" = ok ] && [ -z "$batch_timeout" ] && [ "$batch_retry" = 1 ]; then
	pass 'non-live RADIO chain keeps existing timeout and retry policy'
else
	fail_case "non-live RADIO policy (out=$batch_out timeout=$batch_timeout retry=$batch_retry)"
fi

# 7. Caller-provided timeouts shorter than either total budget stay authoritative.
: >"$ATTEMPT_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=5
prepass_out=$(ai_generate_list 'RADIO:weather:prepass' "$prompt" 'success' 2 2>/dev/null || true)
prepass_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
: >"$ATTEMPT_LOG"
RADIO_MAIN_TOTAL_BUDGET_SEC=5
main_short_out=$(ai_generate_list 'RADIO:weather' "$prompt" 'success' 2 '_radio_is_valid_generation_candidate' 2>/dev/null || true)
main_short_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
if [ "$prepass_out" = ok ] && [ "$prepass_timeout" = 2 ] \
	&& [ "$main_short_out" = ok ] && [ "$main_short_timeout" = 2 ]; then
	pass 'explicit shorter timeout is preserved for prepass and main'
else
	fail_case "explicit shorter timeout (prepass=$prepass_timeout main=$main_short_timeout)"
fi

# 8. Misconfiguration cannot silently restore multi-minute lane ownership.
RADIO_PREPASS_TOTAL_BUDGET_SEC=garbage
prepass_invalid=$(_ai_prepass_total_budget_sec)
RADIO_PREPASS_TOTAL_BUDGET_SEC=99999
prepass_capped=$(_ai_prepass_total_budget_sec)
RADIO_MAIN_TOTAL_BUDGET_SEC=garbage
main_invalid=$(_ai_radio_main_total_budget_sec)
RADIO_MAIN_TOTAL_BUDGET_SEC=99999
main_capped=$(_ai_radio_main_total_budget_sec)
RADIO_QUALITY_REPAIR_TOTAL_BUDGET_SEC=garbage
repair_invalid=$(_ai_radio_mixed_repair_total_budget_sec)
RADIO_QUALITY_REPAIR_TOTAL_BUDGET_SEC=99999
repair_capped=$(_ai_radio_mixed_repair_total_budget_sec)
if [ "$prepass_invalid" = 60 ] && [ "$prepass_capped" = 120 ] \
	&& [ "$main_invalid" = 180 ] && [ "$main_capped" = 240 ] \
	&& [ "$repair_invalid" = 60 ] && [ "$repair_capped" = 120 ]; then
	pass 'radio chain budgets default safely and have hard caps'
else
	fail_case "budget validation (prepass=$prepass_invalid/$prepass_capped main=$main_invalid/$main_capped repair=$repair_invalid/$repair_capped)"
fi

# 9. Exercise the real generation-lane lock around a budgeted fake provider. Once
# the budgeted main chain returns, no stale radio lock remains and the next caller
# can acquire the same lane immediately.
if (
	set -euo pipefail
	ELOOP_LIB_DIR="$ROOT"
	AI_GENERATION_QUEUE_LOCK_DIR="$TMP/real-radio-lane"
	AI_RADIO_QUEUE_MAX_WAIT_SEC=2
	AI_GENERATION_QUEUE_WAIT_SEC=1
	source "$ROOT/lib/ai_generate.sh"
	log() { :; }
	_queued_provider() {
		local delay="${1:-1}"
		sleep "$delay"
		return 1
	}
	_ai_dispatch() {
		local label="$1" _agent="$2" _prompt="$3" timeout_override="${4:-1}"
		_ai_generation_queue_run "$label" _queued_provider "$timeout_override"
	}
	ai_generate_list() {
		local label="$1" prompt_file="$2" raw="$3" timeout_override="${4:-}"
		local agent output rc
		local agents=()
		IFS=',' read -ra agents <<<"$raw"
		for agent in "${agents[@]}"; do
			if output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override"); then rc=0; else rc=$?; fi
			if [ "$rc" -eq 0 ] && [ -n "$output" ]; then printf '%s' "$output"; return 0; fi
		done
		return 1
	}
	source "$ROOT/lib/ai_prepass_budget.sh"
	RADIO_MAIN_TOTAL_BUDGET_SEC=1
	ai_generate_list 'RADIO:news' "$prompt" 'slow,success' '' '_radio_is_valid_generation_candidate' >/dev/null 2>&1 || true
	[ ! -d "$AI_GENERATION_QUEUE_LOCK_DIR" ]
	_ai_generation_queue_run 'RADIO:next' true
	[ ! -d "$AI_GENERATION_QUEUE_LOCK_DIR" ]
); then
	pass 'budgeted main releases real radio lane for next caller'
else
	fail_case 'budgeted main releases real radio lane for next caller'
fi

# 10. Runtime shim must load the budget wrapper after the policy implementation.
policy_line=$(grep -nF 'source "$ELOOP_LIB_DIR/lib/ai_generate_policy.sh"' "$ROOT/eloop_lib.sh" | cut -d: -f1)
budget_line=$(grep -nF 'source "$ELOOP_LIB_DIR/lib/ai_prepass_budget.sh"' "$ROOT/eloop_lib.sh" | cut -d: -f1)
if [ -n "$policy_line" ] && [ -n "$budget_line" ] && [ "$budget_line" -gt "$policy_line" ]; then
	pass 'runtime loads radio budget after ai_generate policy'
else
	fail_case 'runtime load order keeps radio budget after ai_generate policy'
fi

# 11. azumag/docich#993 要件2: a single slow prepass candidate must not spend
# the whole window before the fallback list is reached. Before the per-candidate
# cap, ``greedy`` was handed the entire remaining budget, burned it, and the
# second candidate was skipped before reaching provider dispatch.
#
# The review that followed made the regression explicit: asserting ``lines=2``
# alone cannot catch the *absolute-deadline* variant of the cap, where the
# second candidate only survives because it is clamped to 1s. So pin the
# timeout each candidate actually received (budget=6 -> per-candidate cap 3):
# first ~3s, and the second still gets a real window afterwards.
: >"$ATTEMPT_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=6
unset OPENCODE_ABORT_RETRY
start=$(date +%s)
prepass_out=$(ai_generate_list 'RADIO:news:prepass' "$prompt" 'greedy,success' 2>/dev/null || true)
elapsed=$(( $(date +%s) - start ))
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
first_timeout=$(awk -F'|' 'NR==1 {print $3}' "$ATTEMPT_LOG")
second_timeout=$(awk -F'|' 'NR==2 {print $3}' "$ATTEMPT_LOG")
if [ "$prepass_out" = ok ] && [ "$lines" -eq 2 ] \
	&& [ "$first_timeout" -ge 2 ] && [ "$first_timeout" -le 3 ] \
	&& [ "$second_timeout" -ge 2 ] \
	&& [ "$elapsed" -le 6 ]; then
	pass 'slow first prepass candidate still leaves the fallback a real dispatch'
else
	fail_case "prepass per-candidate cap (out=$prepass_out lines=$lines first_timeout=$first_timeout second_timeout=$second_timeout elapsed=$elapsed)"
fi

# 12. azumag/docich#993 要件1/7: the per-candidate cap never extends the chain
# total budget -- the whole run still finishes inside the same wall-clock window.
: >"$ATTEMPT_LOG"
RADIO_PREPASS_TOTAL_BUDGET_SEC=3
start=$(date +%s)
ai_generate_list 'RADIO:news:prepass' "$prompt" 'greedy,greedy,success' >/dev/null 2>&1 || true
elapsed=$(( $(date +%s) - start ))
lines=$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')
if [ "$elapsed" -le 3 ]; then
	pass 'prepass wall-clock stays inside the total budget with the per-candidate cap'
else
	fail_case "prepass wall-clock exceeded total budget (lines=$lines elapsed=$elapsed)"
fi

printf '# pass=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
