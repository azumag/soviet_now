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
			log "[RADIO:${budget_kind}] total budget exhausted -> skip remaining candidate" >&2
			return 0
		fi
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
	local budget_marker="" budget_exhausted=0
	if [ "${!deadline_var+x}" = x ]; then
		previous_deadline_set=1
		previous_deadline="${!deadline_var}"
	fi
	if [ "${OPENCODE_ABORT_RETRY+x}" = x ]; then
		previous_retry_set=1
		previous_retry="$OPENCODE_ABORT_RETRY"
	fi

	# Keep the marker local to this shell call. Bash dynamic scope makes it visible
	# to the command-substitution subshell running _ai_dispatch, while not exporting
	# the path into provider processes. Failure to allocate it never changes runtime
	# behavior; it only means this diagnostic event is omitted for that chain.
	budget_marker=$(mktemp "${TMPDIR:-/tmp}/soren-radio-budget.XXXXXX" 2>/dev/null || true)
	if [ -n "$budget_marker" ]; then
		printf '0\n' >"$budget_marker" 2>/dev/null || true
	fi
	local AI_RADIO_BUDGET_EXHAUSTED_MARKER="$budget_marker"

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
	rm -f "$budget_marker" 2>/dev/null || true
	_ai_restore_scoped_radio_budget_env "$deadline_var" "$previous_deadline_set" "$previous_deadline" \
		"$previous_retry_set" "$previous_retry"
	if [ "$budget_exhausted" -eq 1 ] && declare -F _ai_stats_record >/dev/null 2>&1; then
		# One fixed event per chain. Label remains the existing component key; no
		# provider/model/prompt/output/path is persisted by this observation.
		_ai_stats_record "budget_exhausted" "$chain_label" "" "" ""
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
