#!/usr/bin/env bash
# Keep non-interactive generation/aggregation behind the live render and audio
# paths on CPU-limited Linux hosts. Called once by each owning worker at startup.
soren_background_priority() {
	case "${OSTYPE:-}" in linux*) ;; *) return 0 ;; esac
	local priority="${SOREN_BACKGROUND_NICE:-10}" current
	case "$priority" in [0-9]|1[0-9]) ;; *) priority=10 ;; esac
	[ "$priority" -eq 0 ] && return 0
	current=$(ps -o ni= -p "$$" 2>/dev/null) || return 0
	current="${current//[[:space:]]/}"
	[[ "$current" =~ ^-?[0-9]+$ ]] || return 0
	# Never try to promote an already lower-priority process (needs privileges).
	[ "$current" -ge "$priority" ] && return 0
	renice -n "$priority" -p "$$" >/dev/null 2>&1 || true
}
