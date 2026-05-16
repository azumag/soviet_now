#!/bin/bash
# system_progress_report.sh - queue a Codex/system-improvement progress report for audio_worker.
#
# Usage:
#   ./system_progress_report.sh "短い進捗本文"

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a
# shellcheck source=/dev/null
source ./eloop_lib.sh

message="${1:-}"
if [ -z "$message" ]; then
	echo "usage: $0 \"progress message\"" >&2
	exit 2
fi

text="システム改善の進捗です。${message}"
enqueue_audio_text "$text" "system_progress" "${SYSTEM_PROGRESS_AUDIO_SPEAKER:-${IMPROVE_AUDIO_SUMMARY_SPEAKER:-}}" || exit 1

if [ -x ./overlay_notify.sh ]; then
	./overlay_notify.sh system "システム改善進捗" "$message" "info" >/dev/null 2>&1 || true
fi

printf '%s\n' "$text"
