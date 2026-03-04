#!/bin/bash
# fetch_news.sh - Yahoo News RSS 複数カテゴリからランダムにニュースを取得
# 出力: tmp/news.txt (ニュース見出し+本文要約 3件)
# 過去に使った見出しは除外して同じニュースを繰り返さない

cd "$(dirname "$0")"
mkdir -p tmp

OUTFILE="tmp/news.txt"
PAST_NEWS="tmp/.past_news_titles.txt"
PAST_NEWS_LINKS="tmp/.past_news_links.txt"

# 複数カテゴリの RSS を使い候補を増やす
RSS_URLS=(
    "https://news.yahoo.co.jp/rss/topics/top-picks.xml"
    "https://news.yahoo.co.jp/rss/topics/domestic.xml"
    "https://news.yahoo.co.jp/rss/topics/world.xml"
    "https://news.yahoo.co.jp/rss/topics/business.xml"
    "https://news.yahoo.co.jp/rss/topics/it.xml"
    "https://news.yahoo.co.jp/rss/topics/science.xml"
)

# ランダムに3カテゴリ選ぶ（毎回違うジャンルから取得）
selected_urls=()
indices=()
for i in "${!RSS_URLS[@]}"; do indices+=("$i"); done

# シャッフル
for ((i=${#indices[@]}-1; i>0; i--)); do
    j=$((RANDOM % (i+1)))
    tmp=${indices[$i]}
    indices[$i]=${indices[$j]}
    indices[$j]=$tmp
done

# 先頭3つ選択
for k in 0 1 2; do
    selected_urls+=("${RSS_URLS[${indices[$k]}]}")
done

# 複数RSSから記事を収集
all_items=""
for url in "${selected_urls[@]}"; do
    xml=$(curl -s --max-time 5 "$url" 2>/dev/null)
    [ -z "$xml" ] && continue

    items=$(echo "$xml" | awk '
        /<item>/  { in_item=1; title=""; link="" }
        /<\/item>/ { if (in_item && title && link) print title "\t" link; in_item=0 }
        in_item && /<title>/ { gsub(/.*<title>/, ""); gsub(/<\/title>.*/, ""); title=$0 }
        in_item && /<link>/  { gsub(/.*<link>/, ""); gsub(/<\/link>.*/, ""); link=$0 }
    ')
    [ -n "$items" ] && all_items="${all_items}${items}"$'\n'
done

if [ -z "$all_items" ]; then
    rm -f "$OUTFILE"
    exit 0
fi

# 過去に使った見出しを読み込み
past_titles=""
[ -f "$PAST_NEWS" ] && past_titles=$(cat "$PAST_NEWS")
past_links=""
[ -f "$PAST_NEWS_LINKS" ] && past_links=$(cat "$PAST_NEWS_LINKS")

# 過去に使ったものを除外（タイトル/URL重複も除外）
available_file=$(mktemp /tmp/eloop_news_available_XXXXXXXX)
seen_titles=""
seen_links=""
while IFS=$'\t' read -r title link; do
    [ -z "$title" ] && continue
    [ -z "$link" ] && continue
    if printf '%s\n' "$seen_titles" | grep -qxF "$title"; then
        continue
    fi
    if printf '%s\n' "$seen_links" | grep -qxF "$link"; then
        continue
    fi
    if printf '%s\n' "$past_links" | grep -qxF "$link"; then
        continue
    fi
    if ! printf '%s\n' "$past_titles" | grep -qxF "$title"; then
        printf '%s\t%s\n' "$title" "$link" >>"$available_file"
    fi
    # NOTE: 文字列中に実改行を入れて保持（\n リテラルだと重複判定できない）
    seen_titles="${seen_titles}${title}"$'\n'
    seen_links="${seen_links}${link}"$'\n'
done <<< "$all_items"

# 新規候補がない場合は履歴を維持したままスキップ
if [ ! -s "$available_file" ]; then
    rm -f "$available_file" "$OUTFILE"
    exit 0
fi

# シャッフルして3件選ぶ（保険としてタイトル重複を再除外）
selected=$(sort -R "$available_file" | awk -F '\t' '!seen[$1]++' | head -3)
rm -f "$available_file"

# 各ニュースの見出し＋本文要約を取得
result=""
while IFS=$'\t' read -r title link; do
    [ -z "$title" ] && continue

    # 使用済みとして記録
    echo "$title" >> "$PAST_NEWS"
    echo "$link" >> "$PAST_NEWS_LINKS"

    # リンク先のog:descriptionから本文要約を取得
    desc=""
    if [ -n "$link" ]; then
        desc=$(curl -sL --max-time 5 "$link" 2>/dev/null | \
            grep -o '<meta property="og:description" content="[^"]*"' | \
            head -1 | sed 's/.*content="//;s/"$//')
    fi

    if [ -n "$desc" ]; then
        result="${result}■ ${title}
${desc}

"
    else
        result="${result}■ ${title}

"
    fi
done <<< "$selected"

# 過去記録は直近100件保持（候補が多いので余裕を持たせる）
tail -100 "$PAST_NEWS" > "${PAST_NEWS}.tmp" && mv "${PAST_NEWS}.tmp" "$PAST_NEWS"
tail -200 "$PAST_NEWS_LINKS" > "${PAST_NEWS_LINKS}.tmp" && mv "${PAST_NEWS_LINKS}.tmp" "$PAST_NEWS_LINKS"

if [ -n "$result" ]; then
    echo "$result" > "$OUTFILE"
else
    rm -f "$OUTFILE"
fi
