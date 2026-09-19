#!/bin/bash
# Bound live RADIO generation chains without changing provider order or queue policy.
#
# ai_generate_list() tries multiple providers sequentially and the OpenCode backend can
# retry one provider internally. Because each dispatch owns the shared radio lane while
# its provider command runs, a required main generation can otherwise occupy that lane
# for several minutes. Optional prepass already has a short total budget; live main
# generation now gets a separate, more generous total wall-clock budget.
#
# The budget applies only to the normal on-air main-generation call, identified by the
# existing _radio_is_valid_generation_candidate validator. Other RADIO users (batch,
# polls, etc.), COMMENT and improvement chains retain their existing policy.

_ai_prepass_total_budget_sec() {
	local value="${RADIO_PREPASS_TOTAL_BUDGET_SEC:-60}"
	case "$value" in
	'' | *[!0-9]*) value=60 ;;
	esac
	[ "$value" -lt 1 ] && value=60
	[ "$value" -gt 120 ] && value=120
	printf '%s\n' "$value"
}

_ai_radio_main_total_budget_sec() {
	local value="${RADIO_MAIN_TOTAL_BUDGET_SEC:-180}"
	case "$value" in
	'' | *[!0-9]*) value=180 ;;
	esac
	[ "$value" -lt 1 ] && value=180
	# Keep required generation below the default 300s radio queue wait while still
	# allowing a slow-but-healthy backend substantially more time than prepass.
	[ "$value" -gt 240 ] && value=240
	printf '%s\n' "$value"
}

# Record only bounded numeric budget facts in a private per-chain marker. The marker
# path is dynamically scoped by _ai_generate_list_with_radio_budget and is never
# exported into provider processes. Each _ai_dispatch call appends one fixed record,
# allowing the outer chain to distinguish calls that really executed from candidates
# skipped only because the total budget had already expired.
_ai_radio_budget_detail_append() {
	local kind="${1:-}" remaining="${2:-0}"
	local detail_file="${AI_RADIO_BUDGET_DETAIL_MARKER:-}"
	[ -n "$detail_file" ] || return 0
	case "$kind" in
	executed | skipped) ;;
	*) return 0 ;;
	esac
	[[ "$remaining" =~ ^-?[0-9]+$ ]] || remaining=0
	printf '%s|%s\n' "$kind" "$remaining" >>"$detail_file" 2>/dev/null || true
}

# eloop_lib.sh reloads ai_generate.sh and ai_generate_policy.sh before this file,
# so these snapshots always point at the current reviewed implementations rather
# than wrappers from an older source pass.
if declare -F ai_generate_list >/dev/null 2>&1; then
	eval "$(declare -f ai_generate_list | sed '1s/^ai_generate_list[[:space:]]*()/_ai_generate_list_without_radio_budget ()/')"
fi
if declare -F _ai_dispatch >/dev/null 2>&1; then
	eval "$(declare -f _ai_dispatch | sed '1s/^_ai_dispatch[[:space:]]*()/_ai_dispatch_without_radio_budget ()/')"
fi

_ai_dispatch() {
	local label="${1:-AI}" agent="${2:-}" prompt_file="${3:-}" timeout_override="${4:-}"
	local deadline="" budget_kind="" now remaining

	case "${label,,}" in
	radio:*:prepass*)
		deadline="${AI_PREPASS_CHAIN_DEADLINE_EPOCH:-}"
		budget_kind="prepass"
		;;
	radio:*)
		# This variable is scoped only around the reviewed live-main ai_generate_list
		# call below, so unrelated RADIO:* consumers do not inherit this budget.
		deadline="${AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH:-}"
		[ -n "$deadline" ] && budget_kind="main"
		;;
	esac

	if [[ "$deadline" =~ ^[0-9]+$ ]]; then
		now=$(date +%s)
		remaining=$((deadline - now))
		if [ "$remaining" -le 0 ]; then
			# Return an empty successful dispatch rather than a provider failure. The
			# policy layer will continue/finish the chain without creating a bogus
			# failure backoff for a candidate that was never executed. A private,
			# dynamically-scoped marker lets the outer chain record this separately
			# from a true all-candidates-executed failure without changing control flow.
			if [ -n "${AI_RADIO_BUDGET_EXHAUSTED_MARKER:-}" ]; then
				printf '1\n' >"$AI_RADIO_BUDGET_EXHAUSTED_MARKER" 2>/dev/null || true
			fi
			_ai_radio_budget_detail_append skipped "$remaining"
			log "[RADIO:${budget_kind}] total budget exhausted -> skip remaining candidate" >&2
			return 0
		fi
		# Capture the remaining chain budget at the moment a real provider dispatch
		# begins. This is bounded by the reviewed 120s/240s chain caps and contains no
		# provider/model/prompt data.
		_ai_radio_budget_detail_append executed "$remaining"
		case "$timeout_override" in
		'' | *[!0-9]*) timeout_override="$remaining" ;;
		*) [ "$timeout_override" -gt "$remaining" ] && timeout_override="$remaining" ;;
		esac
		[ "$timeout_override" -lt 1 ] && timeout_override=1
	fi

	_ai_dispatch_without_radio_budget "$label" "$agent" "$prompt_file" "$timeout_override"
}

_ai_restore_scoped_radio_budget_env() {
	local deadline_var="$1" previous_deadline_set="$2" previous_deadline="$3"
	local previous_retry_set="$4" previous_retry="$5"
	if [ "$previous_deadline_set" -eq 1 ]; then
		export "$deadline_var=$previous_deadline"
	else
		unset "$deadline_var"
	fi
	if [ "$previous_retry_set" -eq 1 ]; then
		export OPENCODE_ABORT_RETRY="$previous_retry"
	else
		unset OPENCODE_ABORT_RETRY
	fi
}

_ai_generate_list_with_radio_budget() {
	local deadline_var="$1" budget="$2"
	shift 2
	local chain_label="${1:-AI}"
	local previous_deadline="" previous_deadline_set=0
	local previous_retry="" previous_retry_set=0 rc
	local budget_marker="" budget_detail_marker="" budget_exhausted=0
	local budget_executed=0 budget_skipped=0 budget_last_remaining=0
	local detail_kind detail_remaining
	if [ "${!deadline_var+x}" = x ]; then
		previous_deadline_set=1
		previous_deadline="${!deadline_var}"
	fi
	if [ "${OPENCODE_ABORT_RETRY+x}" = x ]; then
		previous_retry_set=1
		previous_retry="$OPENCODE_ABORT_RETRY"
	fi

	# Keep markers local to this shell call. Bash dynamic scope makes the paths visible
	# to command-substitution subshells running _ai_dispatch, while the variables are
	# not exported into provider processes. Allocation failure never changes runtime
	# behavior; it only omits the corresponding diagnostic detail.
	budget_marker=$(mktemp "${TMPDIR:-/tmp}/soren-radio-budget.XXXXXX" 2>/dev/null || true)
	if [ -n "$budget_marker" ]; then
		printf '0\n' >"$budget_marker" 2>/dev/null || true
	fi
	budget_detail_marker=$(mktemp "${TMPDIR:-/tmp}/soren-radio-budget-detail.XXXXXX" 2>/dev/null || true)
	if [ -n "$budget_detail_marker" ]; then
		: >"$budget_detail_marker" 2>/dev/null || true
	fi
	local AI_RADIO_BUDGET_EXHAUSTED_MARKER="$budget_marker"
	local AI_RADIO_BUDGET_DETAIL_MARKER="$budget_detail_marker"

	# A backend-internal retry uses the same timeout again and can therefore exceed
	# the chain deadline while holding the lane. Use one attempt per candidate inside
	# a bounded chain; normal retry behavior is restored immediately afterwards.
	export OPENCODE_ABORT_RETRY=0
	export "$deadline_var=$(( $(date +%s) + budget ))"
	if _ai_generate_list_without_radio_budget "$@"; then
		rc=0
	else
		rc=$?
	fi
	if [ -n "$budget_marker" ] && [ "$(cat "$budget_marker" 2>/dev/null || true)" = "1" ]; then
		budget_exhausted=1
	fi
	if [ -n "$budget_detail_marker" ] && [ -f "$budget_detail_marker" ]; then
		while IFS='|' read -r detail_kind detail_remaining; do
			[[ "$detail_remaining" =~ ^-?[0-9]+$ ]] || continue
			case "$detail_kind" in
			executed)
				budget_executed=$((budget_executed + 1))
				[ "$detail_remaining" -gt 0 ] && budget_last_remaining="$detail_remaining"
				;;
			skipped) budget_skipped=$((budget_skipped + 1)) ;;
			esac
		done <"$budget_detail_marker"
	fi
	# Keep persisted counters bounded even if an operator supplies an abnormally long
	# candidate list. Normal chains are far below these caps.
	[ "$budget_executed" -gt 99 ] && budget_executed=99
	[ "$budget_skipped" -gt 99 ] && budget_skipped=99
	[ "$budget_last_remaining" -gt 240 ] && budget_last_remaining=240
	rm -f "$budget_marker" "$budget_detail_marker" 2>/dev/null || true
	_ai_restore_scoped_radio_budget_env "$deadline_var" "$previous_deadline_set" "$previous_deadline" \
		"$previous_retry_set" "$previous_retry"
	if [ "$budget_exhausted" -eq 1 ] && declare -F _ai_stats_record >/dev/null 2>&1; then
		# One fixed event per chain. The error field is a strict numeric grammar for
		# owner-only/sanitized diagnostics: no provider/model/prompt/output/path data.
		_ai_stats_record "budget_exhausted" "$chain_label" "" "" "" \
			"exec=${budget_executed};skip=${budget_skipped};last_budget=${budget_last_remaining};rem=0"
	fi
	return "$rc"
}

ai_generate_list() {
	local label="${1:-AI}" validator="${5:-}" budget
	case "${label,,}" in
	radio:*:prepass*)
		budget=$(_ai_prepass_total_budget_sec)
		_ai_generate_list_with_radio_budget AI_PREPASS_CHAIN_DEADLINE_EPOCH "$budget" "$@"
		return $?
		;;
	radio:*)
		# This validator is the contract used by _radio_generate_and_play for normal
		# on-air main text. Restricting the budget to it avoids changing batch/poll
		# generation that happens to use a RADIO:* label.
		if [ "$validator" = "_radio_is_valid_generation_candidate" ]; then
			budget=$(_ai_radio_main_total_budget_sec)
			_ai_generate_list_with_radio_budget AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH "$budget" "$@"
			return $?
		fi
		;;
	esac
	_ai_generate_list_without_radio_budget "$@"
	return $?
}
