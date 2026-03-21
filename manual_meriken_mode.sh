#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a
source ./eloop_lib.sh

print_status() {
	local mode="off" running="no" improving="no"
	if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
		mode="on"
	fi
	if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
		running="yes"
	fi
	if command -v _is_improve_running >/dev/null 2>&1 && _is_improve_running; then
		improving="yes"
	fi
	printf 'manual_meriken_mode=%s\n' "$mode"
	printf 'soren91_running=%s\n' "$running"
	printf 'improve_running=%s\n' "$improving"
}

usage() {
	cat <<'EOF'
Usage: ./manual_meriken_mode.sh on|off|status
EOF
}

cmd="${1:-status}"
case "$cmd" in
on)
	manual_meriken_mode_enable
	print_status
	printf 'note=中華AI pause は次の soren_loop 周回で反映されます\n'
	;;
off)
	manual_meriken_mode_disable
	print_status
	;;
status)
	print_status
	;;
*)
	usage >&2
	exit 1
	;;
esac
