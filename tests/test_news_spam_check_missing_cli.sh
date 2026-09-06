#!/usr/bin/env bash
# Missing optional spam-check CLI must fail open before consuming the shared radio AI lane.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -n '/^_news_ai_spam_check()/,/^}/p' "$ROOT/broadcast/radio_news.sh" >"$TMP/spam_check.sh"

AI_GENERATION_QUEUE_ENABLED=1
AI_GENERATION_QUEUE_LAST_TOKEN=""
log() { printf '%s\n' "$*" >>"$TMP/log"; }
_ai_generation_queue_enter() {
  printf 'enter %s\n' "$1" >>"$TMP/queue_calls"
  AI_GENERATION_QUEUE_LAST_TOKEN='test-token'
}
_ai_generation_queue_leave() { printf 'leave %s %s\n' "$1" "$2" >>"$TMP/queue_calls"; }
# Keep standard text tools available while guaranteeing claude is absent.
PATH=/usr/bin:/bin
# shellcheck disable=SC1090
. "$TMP/spam_check.sh"

set +e
_news_ai_spam_check 'sample title' $'sample title\nsample body'
rc=$?
set -e
[ "$rc" -eq 1 ] || { echo "FAIL: missing CLI must fail open (rc=$rc)" >&2; exit 1; }
[ ! -e "$TMP/queue_calls" ] || {
  echo 'FAIL: missing CLI consumed the radio AI lane' >&2
  cat "$TMP/queue_calls" >&2
  exit 1
}
grep -q 'unavailable\|利用不可\|見つかりません' "$TMP/log" || {
  echo 'FAIL: missing CLI reason was not logged' >&2
  cat "$TMP/log" >&2
  exit 1
}
echo 'PASS: missing spam-check CLI fails open without queueing'
