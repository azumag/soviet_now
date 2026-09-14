#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/tmp"
cat >"$TMP/tmp/news_meta.json" <<'JSON'
{
  "国会で来年度予算案を審議": {
    "source": "NHK 政治",
    "source_key": "nhk_politics",
    "published_at": "2026-09-14T10:00:00Z"
  },
  "大手メーカーが新工場を稼働": {
    "source": "Google News 日本経済",
    "source_key": "google_news_jp_business",
    "published_at": "2026-09-14T10:00:00Z"
  }
}
JSON

PAST_NEWS_READ_SOURCES="$TMP/past_sources.txt"
: >"$PAST_NEWS_READ_SOURCES"
ELOOP_LIB_DIR="$ROOT"

cd "$TMP"
# shellcheck disable=SC1090
. "$ROOT/broadcast/radio_news_priority.sh"

blocks=$'■ 国会で来年度予算案を審議\n政治本文\n\n■ 大手メーカーが新工場を稼働\n経済本文'

result=$(NEWS_POLITICAL_SHARE=1 _random_pick_news_block "$blocks")
if [ "$result" != $'■ 国会で来年度予算案を審議\n政治本文' ]; then
  echo "political lane was not selected with NEWS_POLITICAL_SHARE=1" >&2
  exit 1
fi

result=$(NEWS_POLITICAL_SHARE=0 _random_pick_news_block "$blocks")
if [ "$result" != $'■ 大手メーカーが新工場を稼働\n経済本文' ]; then
  echo "non-political lane was not selected with NEWS_POLITICAL_SHARE=0" >&2
  exit 1
fi

echo "news political priority integration: OK"
