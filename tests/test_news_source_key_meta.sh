#!/usr/bin/env bash
# 選定ニュースの source_key が news_meta.json（正本）から取れることを検証する。
#
# 修正前: _news_source_key_from_name は名前ベースの case 文で、#289 で追加した
# BBC World / The Guardian World / The New York Times World / NPR World /
# France 24 World / DW World がどのパターンにも一致せず空文字を返し、
# _append_news_read_source が空キーを無視するため past_news_read_sources に
# 残らなかった（docich#2468）。
# 修正後: _news_source_key_for_title が meta の source_key を正本として返し、
# meta に無いときだけ名前ベースへフォールバックする。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NEWS_SRC="$ROOT/broadcast/radio_news.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source（修正後の実コードをそのまま使う） ---
sed -n '/^_news_source_name_for_title()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_source_name.sh"
sed -n '/^_news_source_key_from_name()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_key_from_name.sh"
sed -n '/^_news_source_key_for_title_meta()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_key_meta.sh"
sed -n '/^_news_source_key_for_title()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_key_title.sh"
sed -n '/^_append_news_read_source()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_read_source.sh"
for f in fn_source_name fn_key_from_name fn_key_meta fn_key_title fn_read_source; do
	[ -s "$TMP/$f.sh" ] || { not_ok "extract $f"; exit 1; }
	# shellcheck source=/dev/null
	. "$TMP/$f.sh"
done

PAST_NEWS_READ_SOURCES="$TMP/past_news_read_sources.txt"
: >"$PAST_NEWS_READ_SOURCES"

# --- meta（fetch_news.py が書く形を模した news_meta.json） ---
mkdir -p "$TMP/tmp"
cat >"$TMP/tmp/news_meta.json" <<'META'
{
  "イギリス議会が新法を可決": {
    "source": "BBC World",
    "source_key": "bbc_world",
    "url": "https://feeds.bbci.co.uk/news/world/rss.xml/story1"
  },
  "欧州の気温記録が更新": {
    "source": "The Guardian World",
    "source_key": "guardian_world",
    "url": "https://www.theguardian.com/world/rss/story2"
  },
  "米国経済指標が下落": {
    "source": "Google News Business(EN)",
    "source_key": "google_news_business_en",
    "url": "https://news.google.com/rss/story3"
  },
  "技術が進歩した話": {
    "source": "Global Voices",
    "source_key": "globalvoices_ja",
    "url": "https://globalvoices.org/story4"
  },
  "ウィキニュースの題材": {
    "source": "ウィキニュース",
    "source_key": "wikinews",
    "url": "https://en.wikinews.org/story5"
  },
  "NHKが国会を中継": {
    "source": "NHK 政治",
    "source_key": "",
    "url": "https://www3.nhk.or.jp/news/story6"
  },
  "旧形式の国内ニュース": {
    "source": "ウィキニュース",
    "url": "https://ja.wikinews.org/story7"
  }
}
META

cd "$TMP" || { not_ok "cd TMP"; exit 1; }

# --- 1) 新規海外ソースの source_key が meta から取れる（修正の核心） ---
key=$(_news_source_key_for_title "イギリス議会が新法を可決")
[ "$key" = "bbc_world" ] && ok "BBC World source_key from meta" \
	|| not_ok "BBC World source_key from meta (got: $key)"

key=$(_news_source_key_for_title "欧州の気温記録が更新")
[ "$key" = "guardian_world" ] && ok "Guardian World source_key from meta" \
	|| not_ok "Guardian World source_key from meta (got: $key)"

# --- 2) google_news 系は family に畳まれ、既存履歴キーと整合 ---
key=$(_news_source_key_for_title "米国経済指標が下落")
[ "$key" = "google_news" ] && ok "google_news family folding" \
	|| not_ok "google_news family folding (got: $key)"

key=$(_news_source_key_for_title "技術が進歩した話")
[ "$key" = "globalvoices" ] && ok "globalvoices family folding" \
	|| not_ok "globalvoices family folding (got: $key)"

# --- 3) meta の source_key が空のときだけ名前ベースへフォールバック ---
key=$(_news_source_key_for_title "ウィキニュースの題材")
[ "$key" = "wikinews" ] && ok "wikinews via meta" \
	|| not_ok "wikinews via meta (got: $key)"

key=$(_news_source_key_for_title "NHKが国会を中継")
[ -z "$key" ] && ok "empty key for unknown source" \
	|| not_ok "empty key for unknown source (got: $key)"

# --- 4) meta に source_key 無い旧形式は名前ベースへ、meta ファイル欠落時は空
#     （meta 欠落時は _news_source_name_for_title も名前を取れないため空になり、
#      修正前の挙動と同一。台帳への記録は行われない） ---
key=$(_news_source_key_for_title "旧形式の国内ニュース")
[ "$key" = "wikinews" ] && ok "name fallback for meta without source_key" \
	|| not_ok "name fallback for meta without source_key (got: $key)"

mv tmp/news_meta.json tmp/news_meta.json.bak
key=$(_news_source_key_for_title "ウィキニュースの題材")
[ -z "$key" ] && ok "empty when meta file is missing (same as pre-fix)" \
	|| not_ok "empty when meta file is missing (same as pre-fix) (got: $key)"
mv tmp/news_meta.json.bak tmp/news_meta.json

# --- 5) 空キーは台帳に残らず、非空キーは追記される ---
_append_news_read_source ""
_append_news_read_source "bbc_world"
_append_news_read_source "guardian_world"
lines=$(wc -l <"$PAST_NEWS_READ_SOURCES" | tr -d ' ')
[ "$lines" = "2" ] && ok "ledger keeps non-empty keys only" \
	|| not_ok "ledger keeps non-empty keys only (got: $lines lines)"
grep -qx "bbc_world" "$PAST_NEWS_READ_SOURCES" && ok "bbc_world recorded" \
	|| not_ok "bbc_world recorded"
grep -qx "guardian_world" "$PAST_NEWS_READ_SOURCES" && ok "guardian_world recorded" \
	|| not_ok "guardian_world recorded"

# --- 6) 修正前挙動の再現: 名前ベース case 文だけでは空キーになる（docich#2468） ---
key=$(_news_source_key_from_name "BBC World")
[ -z "$key" ] && ok "legacy name mapping still returns empty for BBC World" \
	|| not_ok "legacy name mapping still returns empty for BBC World (got: $key)"

if [ "$FAIL" -ne 0 ]; then
	echo "FAILED"
	exit 1
fi
echo "PASSED"
