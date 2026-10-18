#!/usr/bin/env bash
# Shell boundary for non-shell AI callers (for example soren91/text_ai.mjs).
# stdout is reserved for the opaque queue token; diagnostics stay on stderr.
# The caller should set AI_GENERATION_QUEUE_OWNER_PID while it holds the token.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELOOP_LIB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export ELOOP_LIB_DIR

cd "$ELOOP_LIB_DIR" || exit 1
if [ -f "$ELOOP_LIB_DIR/core/helpers.sh" ]; then
	# shellcheck disable=SC1091
	source "$ELOOP_LIB_DIR/core/helpers.sh"
fi
# shellcheck disable=SC1091
source "$ELOOP_LIB_DIR/lib/ai_generate.sh"

lane="${2:-}"
case "$lane" in
comment) queue_label="COMMENT:node" ;;
radio) queue_label="RADIO:node" ;;
improve) queue_label="IMPROVE:node" ;;
*)
	printf 'unknown queue lane: %s\n' "$lane" >&2
	exit 2
	;;
esac

case "${1:-}" in
acquire)
	if [ "${AI_GENERATION_QUEUE_ENABLED:-1}" != "1" ]; then
		exit 0
	fi
	_ai_generation_queue_enter "$queue_label"
	rc=$?
	[ "$rc" -eq 0 ] || exit "$rc"
	printf '%s\n' "${AI_GENERATION_QUEUE_LAST_TOKEN:-}"
	;;
release)
	token="${3:-}"
	[ -n "$token" ] || exit 0
	_ai_generation_queue_leave "$token" "$queue_label"
	;;
*)
	printf 'usage: %s acquire|release comment|radio|improve [token]\n' "$0" >&2
	exit 2
	;;
esac
