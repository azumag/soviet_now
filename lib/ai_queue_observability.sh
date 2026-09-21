#!/bin/bash
# lib/ai_queue_observability.sh - generation queue giveup attribution
#
# Wrap the existing queue acquisition function without changing its locking,
# stale-owner reaping, wait budget, or provider fallback semantics.  When the
# bounded wait expires, emit one additional ai_stats event containing only a
# capped wait duration and a fixed holder category.  Raw owner labels, PIDs,
# provider/model names, paths, prompts and command output are never copied into
# this event.

_ai_queue_observability_holder_category() {
	local raw="${1:-}" normalized
	[ -n "$raw" ] || { printf '%s\n' "unknown"; return 0; }
	normalized="${raw,,}"
	case "$normalized" in
	radio:*prepass*) printf '%s\n' "radio_prepass" ;;
	radio*) printf '%s\n' "radio_main" ;;
	news*) printf '%s\n' "news" ;;
	jiji*) printf '%s\n' "jiji" ;;
	celebration*) printf '%s\n' "celebration" ;;
	*) printf '%s\n' "other" ;;
	esac
}

_ai_queue_observability_wait_sec() {
	local started="$1" finished="$2" elapsed cap="${AI_QUEUE_OBSERVABILITY_WAIT_CAP_SEC:-3600}"
	case "$started" in ''|*[!0-9]*) started=0 ;; esac
	case "$finished" in ''|*[!0-9]*) finished="$started" ;; esac
	case "$cap" in ''|*[!0-9]*) cap=3600 ;; esac
	[ "$cap" -lt 1 ] && cap=3600
	[ "$cap" -gt 86400 ] && cap=86400
	elapsed=$((finished - started))
	[ "$elapsed" -lt 0 ] && elapsed=0
	[ "$elapsed" -gt "$cap" ] && elapsed="$cap"
	printf '%s\n' "$elapsed"
}

# eloop_lib.sh can be sourced again in a long-lived radio worker when reviewed
# runtime files change. ai_generate.sh is sourced immediately before this shim,
# so at that point _ai_generation_queue_enter is the newly loaded base function.
# Refresh the saved base on every such reload. If this shim alone is sourced a
# second time, detect our wrapper and keep the existing base to avoid wrapping
# the wrapper recursively.
_ai_queue_observability_refresh_base() {
	local current
	declare -F _ai_generation_queue_enter >/dev/null 2>&1 || return 1
	current=$(declare -f _ai_generation_queue_enter)
	if printf '%s\n' "$current" | grep -q '_ai_generation_queue_enter_base'; then
		return 0
	fi
	eval "$(printf '%s\n' "$current" | sed '1s/_ai_generation_queue_enter/_ai_generation_queue_enter_base/')"
}
_ai_queue_observability_refresh_base || return 1

_ai_generation_queue_enter() {
	local label="${1:-AI}" started finished rc lock_dir owner_label="" holder_category wait_sec
	started=$(date +%s)
	_ai_generation_queue_enter_base "$label"
	rc=$?
	[ "$rc" -eq "${AI_QUEUE_GIVEUP_RC:-92}" ] || return "$rc"

	finished=$(date +%s)
	wait_sec=$(_ai_queue_observability_wait_sec "$started" "$finished")
	holder_category="${AI_GENERATION_QUEUE_LAST_GIVEUP_HOLDER_CATEGORY:-}"
	if [ -z "$holder_category" ]; then
		lock_dir=$(_ai_generation_queue_lock_dir "$label")
		if [ -r "$lock_dir/owner" ]; then
			owner_label=$(sed -n 's/^label=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
		fi
		holder_category=$(_ai_queue_observability_holder_category "$owner_label")
	fi
	AI_GENERATION_QUEUE_LAST_GIVEUP_HOLDER_CATEGORY=""

	# Use a constant component label and fixed error grammar so this record can be
	# safely summarized without exposing the private owner label.
	if declare -F _ai_stats_record >/dev/null 2>&1; then
		_ai_stats_record "queue_giveup_detail" "QUEUE_GIVEUP" "" "$rc" "" \
			"wait=${wait_sec};holder=${holder_category}"
	fi
	return "$rc"
}
