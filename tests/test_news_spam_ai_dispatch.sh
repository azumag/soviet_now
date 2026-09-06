#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CALLS="$TMP/calls"
LOGS="$TMP/logs"
: >"$CALLS"; : >"$LOGS"
export AI_GENERATION_QUEUE_ENABLED=0

log() { printf '%s\n' "$*" >>"$LOGS"; }
ai_generate() {
  printf '%s|%s|%s|%s|%s|%s|gate_wait=%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "${AI_RADIO_IMPROVE_WAIT_MAX_SEC:-unset}" >>"$CALLS"
  printf '%s\n' "${TEST_AI_OUTPUT:-SPAM}"
  return "${TEST_AI_RC:-0}"
}

# shellcheck disable=SC1091
. "$ROOT/broadcast/radio_news.sh"

TEST_AI_OUTPUT=SPAM TEST_AI_RC=0
if ! _news_ai_spam_check "広告記事" $'■ 広告記事\n商品の宣伝です'; then
  echo "spam verdict was not blocked" >&2; exit 1
fi
grep -q '^NEWS:spam_check|' "$CALLS"
grep -q '|local|opencode:muse-spark-1.3-contributor-free|20|_news_spam_verdict_valid|gate_wait=20$' "$CALLS"

: >"$CALLS"
NEWS_SPAM_CHECK_IMPROVE_WAIT_MAX_SEC=3 TEST_AI_OUTPUT=NEWS TEST_AI_RC=0
if _news_ai_spam_check "政策ニュース" $'■ 政策ニュース\n政府発表です'; then
  echo "news verdict was incorrectly blocked" >&2; exit 1
fi
grep -q 'gate_wait=3$' "$CALLS"

: >"$CALLS"
TEST_AI_OUTPUT=garbage TEST_AI_RC=1
if _news_ai_spam_check "判定不能" $'■ 判定不能\n本文です'; then
  echo "AI failure must fail open" >&2; exit 1
fi
grep -q 'AI判定失敗' "$LOGS"

if grep -Eq '(^|[[:space:]])claude([[:space:]]|$)' "$ROOT/broadcast/radio_news.sh"; then
  echo "radio_news still depends on claude CLI" >&2; exit 1
fi

echo "ok"
