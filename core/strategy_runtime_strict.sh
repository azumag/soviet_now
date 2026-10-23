#!/bin/bash
# core/strategy_runtime_strict.sh - production projection hardening layer
#
# Loaded immediately after core/strategy_runtime.sh by eloop_lib.sh.  The base
# runtime keeps its staged-rollout semantics for standalone tests/tools, while
# the real git-less Soren projection must fail closed: inability to prove
# reviewed lineage is never permission to mutate live strategy bytes.

_strategy_runtime_strict_is_projection() {
	case "${SOREN_PROJECTION_FORCE:-auto}" in
	1 | true | yes) return 0 ;;
	esac
	# Deliberately ignore SOREN_PROJECTION_FORCE=0 here.  A real git-less
	# production projection cannot disable its safety boundary with one toggle.
	[ ! -e "$(_strategy_runtime_projection_root)/.git" ]
}

_strategy_runtime_live_apply_policy() {
	local requested="${IMPROVE_LIVE_APPLY_POLICY:-enforce_reviewed}"
	if _strategy_runtime_strict_is_projection; then
		case "$requested" in
		enforce_reviewed)
			printf '%s\n' enforce_reviewed
			;;
		dry_run | off)
			# Unsafe modes require a second, explicit acknowledgement on a real
			# projection.  A stale rollout value in .env is therefore harmless.
			if [ "${SOREN_UNSAFE_PROJECTION_APPLY_ACK:-0}" = "1" ]; then
				printf '%s\n' "$requested"
			else
				printf '%s\n' enforce_reviewed
			fi
			;;
		*)
			printf '%s\n' enforce_reviewed
			;;
		esac
		return 0
	fi

	# Git checkouts keep the original developer/test-friendly semantics.
	case "$requested" in
	enforce_reviewed | dry_run | off) printf '%s\n' "$requested" ;;
	*) printf '%s\n' dry_run ;;
	esac
}

_strategy_runtime_strict_refuse() {
	local policy="$1" reason="$2" source_file="$3" helpers_source="${4:-}" tag="${5:-apply}"
	local quarantine_dir=""
	if [ -f "$source_file" ]; then
		quarantine_dir=$(_strategy_runtime_quarantine_candidate "$source_file" "$helpers_source") || quarantine_dir=""
	fi
	_strategy_runtime_record_apply_failure "projection_guard_refused"
	_strategy_runtime_record_guard_event "refused" "$policy" "$reason" "$quarantine_dir"
	_strategy_runtime_log "[${tag}] fail-closed projection guard refusal (${reason}); candidate preserved at ${quarantine_dir:-<quarantine unavailable>}"
	return "$_STRATEGY_RUNTIME_GUARD_REFUSED"
}

strategy_runtime_projection_apply_gate() {
	# Strict production override of the base gate.
	#   0 = reviewed / explicitly acknowledged unsafe mode
	#   1 = invalid invocation
	#   3 = refused and (when possible) quarantined
	local source_file="${1:-}" target_file="${2:-}" helpers_source="${3:-}" tag="${4:-apply}"
	local policy repo rel verdict quarantine_dir reviewed_mode

	_STRATEGY_RUNTIME_TARGET_MODE=""
	policy=$(_strategy_runtime_live_apply_policy)

	_strategy_runtime_strict_is_projection || return 0
	[ "$policy" = "off" ] && return 0
	{ [ -n "$source_file" ] && [ -f "$source_file" ]; } || return 1
	[ -n "$target_file" ] || return 1

	repo=$(_strategy_runtime_lineage_repo) || {
		if [ "$policy" = "enforce_reviewed" ]; then
			_strategy_runtime_strict_refuse "$policy" "lineage repository unavailable" "$source_file" "$helpers_source" "$tag"
			return $?
		fi
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "lineage repository unavailable" ""
		_strategy_runtime_log "[${tag}] lineage repository unavailable → acknowledged dry_run degrade"
		return 0
	}

	rel=$(_strategy_runtime_rel_to_projection_root "$target_file") || {
		if [ "$policy" = "enforce_reviewed" ]; then
			_strategy_runtime_strict_refuse "$policy" "target outside projection root" "$source_file" "$helpers_source" "$tag"
			return $?
		fi
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "target outside projection root" ""
		_strategy_runtime_log "[${tag}] target outside projection root → acknowledged dry_run degrade"
		return 0
	}

	verdict=$(_strategy_runtime_lineage_verdict "$repo" "$source_file" "$rel" "$helpers_source" "strategy_helpers")
	case "$verdict" in
	reviewed)
		return 0
		;;
	reviewed\ *)
		reviewed_mode="${verdict#reviewed }"
		case "$reviewed_mode" in
		'' | *[!0-7]*) reviewed_mode="" ;;
		esac
		_STRATEGY_RUNTIME_TARGET_MODE="$reviewed_mode"
		return 0
		;;
	mismatch:*)
		if [ "$policy" = "enforce_reviewed" ]; then
			_strategy_runtime_strict_refuse "$policy" "$verdict" "$source_file" "$helpers_source" "$tag"
			return $?
		fi
		_strategy_runtime_record_guard_event "would_refuse" "$policy" "$verdict" ""
		_strategy_runtime_log "[${tag}] acknowledged dry_run — unreviewed live apply would be refused (${verdict})"
		return 0
		;;
	unavailable)
		if [ "$policy" = "enforce_reviewed" ]; then
			_strategy_runtime_strict_refuse "$policy" "reviewed ref unavailable" "$source_file" "$helpers_source" "$tag"
			return $?
		fi
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "reviewed ref unavailable" ""
		_strategy_runtime_log "[${tag}] reviewed ref unavailable → acknowledged dry_run degrade"
		return 0
		;;
	*)
		if [ "$policy" = "enforce_reviewed" ]; then
			_strategy_runtime_strict_refuse "$policy" "unexpected verdict: ${verdict}" "$source_file" "$helpers_source" "$tag"
			return $?
		fi
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "unexpected verdict: ${verdict}" ""
		_strategy_runtime_log "[${tag}] unexpected guard verdict → acknowledged dry_run degrade"
		return 0
		;;
	esac
}
