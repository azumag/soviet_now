#!/bin/bash
# Runtime safety guard for the long-form RADIO generation budget.
#
# RADIO_CODEX_TIMEOUT is an operator override loaded from .env before this file.
# Historical 20-second values are no longer compatible with the current long-form
# radio pipeline and cause healthy providers to be killed before they can answer.
# Keep normal tuning available, but fail safe to the reviewed 240-second default
# when the configured value is malformed or below the minimum useful budget.
#
# Explicit per-call timeout overrides remain authoritative because ai_generate.sh
# applies those before consulting RADIO_CODEX_TIMEOUT. This therefore does not
# affect intentionally short auxiliary calls such as NEWS:spam_check.

_normalize_radio_codex_timeout() {
	local configured="${RADIO_CODEX_TIMEOUT:-}"
	local minimum=60

	# Unset preserves ai_generate.sh's own reviewed default (240s).
	[ -n "$configured" ] || return 0

	case "$configured" in
	'' | *[!0-9]*) RADIO_CODEX_TIMEOUT=240 ;;
	*)
		if [ "$configured" -lt "$minimum" ]; then
			RADIO_CODEX_TIMEOUT=240
		fi
		;;
	esac
	export RADIO_CODEX_TIMEOUT
}

_normalize_radio_codex_timeout
