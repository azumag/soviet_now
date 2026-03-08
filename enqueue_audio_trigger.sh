#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

cmd="${1:-}"
case "$cmd" in
news|soviet|strategy|theme|recap) ;;
*)
	echo "Usage: $0 {news|soviet|strategy|theme|recap}" >&2
	exit 1
	;;
esac

queue_dir="tmp/.manual_audio_triggers"
mkdir -p "$queue_dir"

queue_file="$queue_dir/$(date +%s)_${cmd}_${RANDOM}.cmd"
printf '%s\n' "$cmd" >"$queue_file"
echo "$queue_file"
