#!/bin/bash
# Regression: overlay_notify.sh must reject off-list categories instead of
# poisoning the shared overlay queue. On 2026-09-10 a category='improve'
# line broke every strict docich append until manual repair.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/overlay-notify-category.XXXXXX")"
cleanup() {
	rm -rf "$test_root"
}
trap cleanup EXIT

export EVENT_OVERLAY_EVENTS_FILE="$test_root/overlay_events.jsonl"
export EVENT_OVERLAY_KEEP_EVENTS=10
export EVENT_OVERLAY_HTML_FILE="$test_root/overlay.html"

# Unknown category fails closed and writes nothing.
if "$repo_root/overlay_notify.sh" improve "改善DBを自動修復" "poison" "warn" 2>"$test_root/stderr.txt"; then
	echo "improve category was accepted (must be rejected)" >&2
	exit 1
fi
[ ! -e "$test_root/overlay_events.jsonl" ]
grep -q "unknown category" "$test_root/stderr.txt"

# Every allowlisted category is accepted (keep in sync with docich
# OVERLAY_CATEGORIES).
for category in game worker chat radio prediction rollback system deadline; do
	"$repo_root/overlay_notify.sh" "$category" "t-$category" "b" "info" >/dev/null 2>&1
done
for category in game worker chat radio prediction rollback system deadline; do
	grep -q "\"category\":\"$category\"" "$test_root/overlay_events.jsonl"
done

echo "overlay notify category tests passed"
