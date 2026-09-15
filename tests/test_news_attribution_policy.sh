#!/usr/bin/env bash
# ニュースの出典は読み上げず、帰属表示は字幕・チャット側だけで行うことを検証する。
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

if printf '%s' "$prepared" | grep -q '出典:'; then
	not_ok 'no source line is exposed to the generation prompt'
else
	ok 'no source line is exposed to the generation prompt'
fi
if printf '%s' "$prepared" | grep -q '公開日時:'; then
	not_ok 'no published-at line is exposed to the generation prompt'
else
	ok 'no published-at line is exposed to the generation prompt'
fi
if printf '%s' "$prepared" | grep -q '国内政策ニュース' && printf '%s' "$prepared" | grep -q 'RSS概要です。'; then
	ok 'non-attribution news material remains available for reconstruction'
else
	not_ok 'non-attribution news material remains available for reconstruction'
fi
if printf '%s' "$prepared" | grep -q '海外の市民社会ニュース' && printf '%s' "$prepared" | grep -q 'RSS summary.'; then
	ok 'Global Voices material remains available for reconstruction'
else
	not_ok 'Global Voices material remains available for reconstruction'
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

non_gv_talk="$TMP/non_gv_talk.txt"
cat >"$non_gv_talk" <<'EOF'
国内政策ニュースです。
出典はGoogle News 日本政治です。
本文は再構成されています。
EOF
if _strip_spoken_news_attribution_file "$non_gv_talk" &&
	! grep -q '^出典' "$non_gv_talk" && grep -q '本文は再構成されています。' "$non_gv_talk"; then
	ok 'playback guard removes non-Global Voices spoken attribution'
else
	not_ok 'playback guard removes non-Global Voices spoken attribution'
fi

gv_talk="$TMP/gv_talk.txt"
cat >"$gv_talk" <<'EOF'
海外の市民社会ニュースです。
出典はGlobal Voices(EN)です。
本文です。
EOF
if _strip_spoken_news_attribution_file "$gv_talk" &&
	! grep -q '^出典' "$gv_talk" && grep -q '本文です。' "$gv_talk"; then
	ok 'playback guard removes Global Voices spoken attribution too'
else
	not_ok 'playback guard removes Global Voices spoken attribution too'
fi

if [ "$(grep -c '出典名・媒体名・配信元・URL・公開日時は一切読み上げないこと' "$ROOT/broadcast/radio_corners.sh")" -ge 2 ]; then
	ok 'generated and self-searched news prompts enforce the same no-source policy'
else
	not_ok 'generated and self-searched news prompts enforce the same no-source policy'
fi

exit "$FAIL"
