#!/bin/bash
# lib/poll_wait.sh - forkless poll waits for resident workers (#970)
#
# Resident workers split their poll interval into small slices so they can
# notice `tmp/stop` (and the operator pause file) promptly. Doing that with
# /bin/sleep forks one child process per slice, per worker. The 2026-10-07
# production CPU profile attributed the single largest process-spawn group to
# `sleep <- worker:*` (audio/chat/kick/youtube/poll/watchdog/bgm, ~9/s
# host-wide), i.e. the spawn churn was larger than the actual waiting.
#
# This helper waits the same wall-clock slices without forking: it holds a
# private FIFO open read-write and blocks on bash's `read -t` builtin, which is
# interruptible by signals exactly like the previous per-second slices.
#
# Contract:
#   - docich_poll_wait_open      idempotently open the private wait descriptor
#   - docich_poll_sleep <secs>   wait ~<secs> seconds (>=1), never fails, no child
#   - docich_poll_interval ...   validate a configured loop interval (>=1s)
#
# When the builtin path is not available (no mkfifo, unusable descriptor, or a
# non-bash shell) both helpers degrade to `sleep`, so callers keep working.
#
# The FIFO is unlinked right after it is opened, so nothing is left behind in
# the runtime tree, concurrent workers never share a wait descriptor, and the
# script-owned descriptor 8 does not collide with the flock descriptor 9 that
# bgm_worker.sh holds.
#
# Callers that already own descriptor 8 can pass DOCICH_POLL_WAIT_FD to move it.

_DOCICH_POLL_WAIT_FD="${DOCICH_POLL_WAIT_FD:-8}"
_DOCICH_POLL_WAIT_READY=""

docich_poll_wait_open() {
	[ -n "$_DOCICH_POLL_WAIT_READY" ] && return 0

	local dir fifo probe
	dir="${DOCICH_POLL_WAIT_DIR:-${TMPDIR:-/tmp}}"
	fifo=$(mktemp -u "${dir%/}/docich-poll-wait.XXXXXX" 2>/dev/null) || return 1
	[ -n "$fifo" ] || return 1
	if ! mkfifo "$fifo" 2>/dev/null; then
		rm -f "$fifo" 2>/dev/null || true
		return 1
	fi
	# Opening the FIFO read-write keeps a writer (this shell) alive, so a
	# blocking read never sees EOF and never falls through early.
	if ! eval "exec ${_DOCICH_POLL_WAIT_FD}<>\"\$fifo\"" 2>/dev/null; then
		rm -f "$fifo" 2>/dev/null || true
		return 1
	fi
	rm -f "$fifo" 2>/dev/null || true

	# Verify the descriptor is usable before trusting it: an unusable
	# descriptor makes `read` print a diagnostic instead of just timing out.
	probe=$(read -r -t 0 -u "$_DOCICH_POLL_WAIT_FD" _docich_poll_probe 2>&1)
	if [ -n "$probe" ]; then
		return 1
	fi

	_DOCICH_POLL_WAIT_READY="1"
	return 0
}

# docich_poll_interval <raw> <default> <min>
#
# Validate a configured poll interval before it reaches `sleep`. Issue #2688
# observed a transient python3 poll loop burning a core: a loop whose interval
# came from the environment can become `sleep 0` (or an invalid value that
# makes `sleep` fail immediately), turning a 30s poll into a busy loop that
# spawns python3 back-to-back. Keep every loop's wait strictly positive.
#
# Contract:
#   - empty / non-numeric -> <default>
#   - numeric but below <min> (including 0) -> <min>
#   - <min> itself is normalized to at least 1 second
docich_poll_interval() {
	local raw="${1:-}" default="${2:-1}" min="${3:-1}" value
	case "$default" in
	'' | *[!0-9]*) default=1 ;;
	esac
	case "$min" in
	'' | *[!0-9]*) min=1 ;;
	esac
	[ "$min" -ge 1 ] || min=1
	[ "$default" -ge "$min" ] || default="$min"
	case "$raw" in
	'' | *[!0-9]*) value="$default" ;;
	*) value="$raw" ;;
	esac
	[ "$value" -ge "$min" ] || value="$min"
	printf '%s\n' "$value"
}

docich_poll_sleep() {
	local secs="${1:-1}"
	case "$secs" in
	'' | *[!0-9]*) secs=1 ;;
	esac
	# A busy loop is never a valid poll wait (#2688): 0/negative intervals
	# used to return immediately and let callers spin on python3/state reads.
	[ "$secs" -ge 1 ] 2>/dev/null || secs=1

	if [ -z "$_DOCICH_POLL_WAIT_READY" ] && ! docich_poll_wait_open; then
		sleep "$secs" 2>/dev/null || true
		return 0
	fi

	local _remaining="$secs" _dummy
	while [ "$_remaining" -gt 0 ]; do
		read -r -t 1 -u "$_DOCICH_POLL_WAIT_FD" _dummy 2>/dev/null || true
		_remaining=$((_remaining - 1))
	done
	return 0
}
