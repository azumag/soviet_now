#!/bin/bash
# Bound optional RADIO:*:prepass chains without changing main-generation policy.
#
# ai_generate_list() tries multiple providers sequentially. RADIO candidates normally
# have long per-provider timeouts, so an optional prepass can otherwise hold the
# shared radio lane for several minutes before the required main generation starts.
# This shim gives one prepass chain a single wall-clock budget and clamps each
# dispatch to the remaining time. Once the budget is exhausted, later candidates
# return the existing rc=93 "not attempted" outcome so they do not create bogus
# provider-failure backoff entries. Main/comment/improvement chains are unchanged.

_ai_prepass_total_budget_sec() {
	local value="${RADIO_PREPASS_TOTAL_BUDGET_SEC:-60}"
	case "$value" in
	'' | *[!0-9]*) value=60 ;;
	esac
	[ "$value" -lt 1 ] && value=60
	# Optional research must never regain multi-minute ownership because of a
	# configuration typo. The required main generation has its own separate policy.
	[ "$value" -gt 120 ] && value=120
	printf '%s\n' "$value"
}

# eloop_lib.sh reloads ai_generate.sh and ai_generate_policy.sh before this file,
# so these snapshots always point at the current reviewed implementations rather
# than at wrappers from an older source pass.
if declare -F ai_generate_list >/dev/null 2>&1; then
	eval "$(declare -f ai_generate_list | sed '1s/^ai_generate_list[[:space:]]*()/_ai_generate_list_without_prepass_budget ()/')"
fi
if declare -F _ai_dispatch >/dev/null 2>&1; then
	eval "$(declare -f _ai_dispatch | sed '1s/^_ai_dispatch[[:space:]]*()/_ai_dispatch_without_prepass_budget ()/')"
fi

_ai_dispatch() {
	local label="${1:-AI}" agent="${2:-}" prompt_file="${3:-}" timeout_override="${4:-}"
	local deadline="${AI_PREPASS_CHAIN_DEADLINE_EPOCH:-}" now remaining

	case "${label,,}" in
	radio:*:prepass*)
		if [[ "$deadline" =~ ^[0-9]+$ ]]; then
			now=$(date +%s)
			remaining=$((deadline - now))
			if [ "$remaining" -le 0 ]; then
				log "[RADIO:prepass] total budget exhausted -> skip remaining optional candidates" >&2
				return 93
			fi
			case "$timeout_override" in
			'' | *[!0-9]*) timeout_override="$remaining" ;;
			*) [ "$timeout_override" -gt "$remaining" ] && timeout_override="$remaining" ;;
			esac
			[ "$timeout_override" -lt 1 ] && timeout_override=1
		fi
		;;
	esac

	_ai_dispatch_without_prepass_budget "$label" "$agent" "$prompt_file" "$timeout_override"
}

ai_generate_list() {
	local label="${1:-AI}" budget previous_deadline previous_deadline_set=0 rc
	case "${label,,}" in
	radio:*:prepass*)
		budget=$(_ai_prepass_total_budget_sec)
		if [ "${AI_PREPASS_CHAIN_DEADLINE_EPOCH+x}" = x ]; then
			previous_deadline_set=1
			previous_deadline="$AI_PREPASS_CHAIN_DEADLINE_EPOCH"
		fi
		export AI_PREPASS_CHAIN_DEADLINE_EPOCH=$(( $(date +%s) + budget ))
		_ai_generate_list_without_prepass_budget "$@"
		rc=$?
		if [ "$previous_deadline_set" -eq 1 ]; then
			export AI_PREPASS_CHAIN_DEADLINE_EPOCH="$previous_deadline"
		else
			unset AI_PREPASS_CHAIN_DEADLINE_EPOCH
		fi
		return "$rc"
		;;
	*)
		_ai_generate_list_without_prepass_budget "$@"
		return $?
		;;
	esac
}
