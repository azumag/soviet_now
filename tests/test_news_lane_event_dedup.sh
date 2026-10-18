#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/tmp"
: >"$TMP/tmp/news_meta.json"

PAST_NEWS_READ="$TMP/past_titles.txt"
PAST_NEWS_READ_KEYS="$TMP/past_keys.txt"
PAST_NEWS_TOPIC_KEYS="$TMP/past_topic_keys.txt"
PAST_NEWS_URL_HASHES="$TMP/past_urls.txt"
: >"$PAST_NEWS_READ"
: >"$PAST_NEWS_READ_KEYS"
: >"$PAST_NEWS_TOPIC_KEYS"
: >"$PAST_NEWS_URL_HASHES"
ELOOP_LIB_DIR="$ROOT"

# shellcheck disable=SC1090
. "$ROOT/broadcast/radio_news.sh"

blocks=$'■ 台風17号上陸、沖縄で住宅浸水と避難指示\n本文A\n\n■ 鹿児島に台風17号、避難所を開設\n本文B\n\n■ 台風17号の影響、九州で大規模停電\n本文C'
result=$(_filter_unread_news_blocks <<<"$blocks")
count=$(grep -c '^■ ' <<<"$result" || true)
if [ "$count" -ne 1 ] || ! grep -qF '台風17号上陸、沖縄で住宅浸水と避難指示' <<<"$result"; then
  echo "same numbered storm was not collapsed within the news lane" >&2
  printf '%s\n' "$result" >&2
  exit 1
fi

printf '%s\n' '台風17号上陸、沖縄で住宅浸水と避難指示' >"$PAST_NEWS_READ"
result=$(_filter_unread_news_blocks <<<"$blocks")
if [ -n "$result" ]; then
  echo "numbered storm variant survived past-title dedup" >&2
  printf '%s\n' "$result" >&2
  exit 1
fi

echo "news lane event dedup: OK"
