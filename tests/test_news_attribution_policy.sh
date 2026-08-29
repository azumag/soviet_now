#!/usr/bin/env bash
# ニュースの出典表示は Global Voices 系列だけに限定されることを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

mkdir -p "$TMP/tmp"
cd "$TMP"
PAST_NEWS_READ_SOURCES="$TMP/past_sources.txt"
: >"$PAST_NEWS_READ_SOURCES"

. "$ROOT/broadcast/radio_news.sh"
. "$ROOT/core/phyrogenetic.sh"

cat >tmp/news_meta.json <<'JSON'
{
  "国内政策ニュース": {
    "source": "Google News 日本政治",
    "source_key": "google_news_jp_politics",
    "published_at": "2026-08-30T01:00:00Z",
    "url": "https://example.test/google",
    "license": "RSS"
  },
  "海外の市民社会ニュース": {
    "source": "Global Voices(EN)",
    "source_key": "globalvoices_en",
    "lang": "en",
    "published_at": "2026-08-30T02:00:00Z",
    "url": "https://example.test/globalvoices",
    "license": "CC BY 3.0",
    "author": "Example Author"
  }
}
JSON

blocks=$'■ 国内政策ニュース\nRSS概要です。\n\n■ 海外の市民社会ニュース\nRSS summary.'
prepared="$(_prepare_news_prompt_blocks "$blocks")"

if printf '%s' "$prepared" | grep -q '出典: Google News'; then
	not_ok 'Google News source is hidden from the generation prompt'
else
	ok 'Google News source is hidden from the generation prompt'
fi
if printf '%s' "$prepared" | grep -q '出典: Global Voices(EN) \[英語\]'; then
	ok 'Global Voices source remains in the generation prompt'
else
	not_ok 'Global Voices source remains in the generation prompt'
fi
if printf '%s' "$prepared" | grep -q '国内政策ニュース' && printf '%s' "$prepared" | grep -q 'RSS概要です。'; then
	ok 'non-attribution news material remains available for reconstruction'
else
	not_ok 'non-attribution news material remains available for reconstruction'
fi

if [ -z "$(_extract_news_source_name '国内政策ニュース')" ]; then
	ok 'spoken attribution is empty for non-Global Voices news'
else
	not_ok 'spoken attribution is empty for non-Global Voices news'
fi
if [ "$(_extract_news_source_name '海外の市民社会ニュース')" = 'Global Voices(EN)' ]; then
	ok 'spoken attribution remains for Global Voices news'
else
	not_ok 'spoken attribution remains for Global Voices news'
fi

if [ -z "$(_build_cc_attribution_text '国内政策ニュース')" ]; then
	ok 'caption and chat attribution are empty for non-Global Voices news'
else
	not_ok 'caption and chat attribution are empty for non-Global Voices news'
fi
if _build_cc_attribution_text '海外の市民社会ニュース' | grep -q 'Global Voices(EN)'; then
	ok 'caption and chat attribution remain for Global Voices news'
else
	not_ok 'caption and chat attribution remain for Global Voices news'
fi

if [ "$(grep -c '出典名を読み上げるのはGlobal Voicesの記事を扱う場合だけ' "$ROOT/broadcast/radio_corners.sh")" -ge 2 ]; then
	ok 'generated and self-searched news prompts enforce the same policy'
else
	not_ok 'generated and self-searched news prompts enforce the same policy'
fi

exit "$FAIL"
