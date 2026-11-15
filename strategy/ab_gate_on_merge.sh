#!/usr/bin/env bash
# strategy/ab_gate_on_merge.sh - A/B merge/reflect safety gate
#
# Reports whether an A/B experiment (selection stage OR confirmation stage)
# is currently active, so that a strategy.py change (PR merge or live
# reflection into the running agent) never happens while an A/B is still
# running.
#
# Exit codes:
#   0  no active A/B detected -> safe to merge / reflect
#   1  an active A/B IS detected -> fail-closed (abort merge / reflect)
#   2  indeterminate (unreadable state file) -> fail-closed
#
# Evidence sources (any one is sufficient to report "active"):
#   - tmp/state/ab_state.json exists, carries a_hash or b_hash, and no
#     ab_abort marker exists (an experiment is recorded and not aborted)
#   - SOREN_AB_ALT_STRATEGY is non-empty in the live shell (an arm is
#     selected for the next game)
#   - .env carries REGRESSION_DISABLED=1 (the A/B gate is enabled)
#   - tmp/state/improve_daemon.paused exists (improve paused for A/B)
#
# Deliberately NOT a signal: improve.lock. The real path is
# tmp/improve.lock (core/config.sh: IMPROVE_LOCK_FILE), not tmp/state/, and
# the repository convention treats it as a game-history data file that is
# normally present, so it cannot mark an A/B (lib/ai_generate.sh,
# broadcast/radio_engine.sh). ab_interleave.sh actually requires
# "improve_daemon.paused present AND improve.lock absent" for A/B.
#
# Runtime hooks (source with AB_STATE_FILE, AB_ENV_FILE, and the tmp/state
# directory already configured, or call check_without_state():
#   ab_gate_on_merge_check   -> 0 = clear, 1 = active A/B, 2 = unknown
#   ab_gate_on_merge_status  -> human-readable status line (to stderr/stdout)
#
# Usage:
#   ab_gate_on_merge.sh check       # exit status only
#   ab_gate_on_merge.sh status      # print a status line
#   ab_gate_on_merge.sh init        # nothing (API seed; intentionally a no-op)
set -u

# --- defaults (match ab_interleave.sh / ab_ctl.sh conventions) -------------
: "${AB_STATE_FILE:=${TMP_STATE_DIR:-tmp/state}/ab_state.json}"
: "${AB_ENV_FILE:=.env}"
: "${AB_ABORT_FILE:=${TMP_STATE_DIR:-tmp/state}/ab_abort}"
: "${AB_ACTIVE_BRANCH_FILE:=${TMP_STATE_DIR:-tmp/state}/active_branch.json}"

_ab_gate_on_merge_file_exists() {
	[ -f "$1" ] || return 1
	# unreadable state file -> indeterminate, fail-closed (2)
	if [ ! -r "$1" ]; then return 2; fi
	local n
	n=$(wc -c < "$1" 2>/dev/null || echo 0)
	[ "$n" -gt 0 ]
}

_ab_gate_on_merge_contains() { # file key
	python3 - "$1" "$2" <<'PY'
import json, os, sys
p, key = sys.argv[1:]
try:
    v = json.load(open(p, encoding="utf-8")).get(key)
    print("" if v is None else v)
except Exception:
    print("")
PY
}

# --- main check ------------------------------------------------------------
# Sets: AB_GATE_ON_MERGE_RC (0=clear,1=active,2=unknown)
_ab_gate_on_merge_check() {
	local rc=0

	# 1) recorded experiment: ab_state.json with a_hash/b_hash and no abort
	local file_rc=0
	_ab_gate_on_merge_file_exists "$AB_STATE_FILE" || file_rc=$?
	if [ "$file_rc" -eq 2 ]; then
		# present but unreadable: fail-closed, do not fall through to the
		# other (possibly clear) signals.
		AB_GATE_ON_MERGE_RC=2
		return 0
	fi
	if [ "$file_rc" -eq 0 ]; then
		local a b
		a=$(_ab_gate_on_merge_contains "$AB_STATE_FILE" "a_hash")
		b=$(_ab_gate_on_merge_contains "$AB_STATE_FILE" "b_hash")
		if [ -n "$a" ] || [ -n "$b" ]; then
			if [ -f "$AB_ABORT_FILE" ]; then
				# recorded but manually aborted -> not active (succeed=normal)
				AB_GATE_ON_MERGE_RC=0
				return 0
			fi
			AB_GATE_ON_MERGE_RC=1
			return 0
		fi
	fi

	# 2) live arm selected
	if [ -n "${SOREN_AB_ALT_STRATEGY:-}" ]; then
		AB_GATE_ON_MERGE_RC=1
		return 0
	fi

	# 3) gate enabled in .env
	# REGRESSION_DISABLED=1 is not an A/B-exclusive marker (it has been used
	# to freeze other improvements too), so this signal can over-detect. That
	# is accepted here: the gate is fail-closed and an unnecessary block is
	# preferable to merging under a live A/B.
	if [ -f "$AB_ENV_FILE" ]; then
		if grep -Eq "^REGRESSION_DISABLED=(1|true|TRUE)" "$AB_ENV_FILE" 2>/dev/null; then
			AB_GATE_ON_MERGE_RC=1
			return 0
		fi
	fi

	# 4) improve paused for A/B
	if [ -f "${TMP_STATE_DIR:-tmp/state}/improve_daemon.paused" ]; then
		AB_GATE_ON_MERGE_RC=1
		return 0
	fi

	AB_GATE_ON_MERGE_RC=0
	return 0
}

_ab_gate_on_merge_status() {
	_ab_gate_on_merge_check
	case "$AB_GATE_ON_MERGE_RC" in
		0) printf 'AB:GATE CLEAR (no active A/B) -> safe to merge/reflection\n' ;;
		1) printf 'AB:GATE ACTIVE A/B detected -> do NOT merge/reflection\n' ;;
		2) printf 'AB:GATE UNKNOWN (unreadable state) -> treat as active (fail-closed)\n' ;;
		*) printf 'AB:GATE BAD-RC %s\n' "$AB_GATE_ON_MERGE_RC" ;;
	esac
}

_ab_gate_on_merge_help() {
	# print the leading comment block (kept in sync automatically)
	awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

_ab_gate_on_merge_main() {
	case "${1:-}" in
		check)
			_ab_gate_on_merge_check
			return "$AB_GATE_ON_MERGE_RC"
			;;
		status)
			_ab_gate_on_merge_status
			;;
		init)
			# API seed: intentionally a no-op so callers that only want to
			# confirm the hook exists can do so without side effects.
			return 0
			;;
		*)
			echo "usage: $0 {check|status|init}" >&2
			return 2
			;;
	esac
}

_ab_gate_on_merge_main "$@"
