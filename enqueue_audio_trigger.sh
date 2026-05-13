#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

cmd="${1:-}"
case "$cmd" in
news | soviet | strategy | theme | weather | fortune | market | dinner | deals | survival | jiji | rakugo | health | wiki | sightseeing | lunch | fortune | devil_dict | soviet_quiz | bluegrass | breakfast | dinner | whatday | survival | night_snack | local_japan | redefine | soviet_lifehack | world_dinner | zaitech | fudosan) ;;
*)
	echo "Usage: $0 {news|soviet|strategy|theme|weather|fortune|market|dinner|deals|survival|jiji|...}" >&2
	exit 1
	;;
esac

queue_dir="tmp/.manual_audio_triggers"
mkdir -p "$queue_dir"

# dedup: 同一 corner で未処理の .cmd が既にあればスキップ
for existing in "$queue_dir"/*_"${cmd}".cmd; do
	[ -e "$existing" ] && echo "skip (already queued): $existing" && exit 0
done

queue_file="$queue_dir/$(date +%s)_${cmd}_${RANDOM}.cmd"
printf '%s\n' "$cmd" >"$queue_file"
echo "$queue_file"
