#!/bin/bash
# fetch_news.sh - Yahoo News RSSからランダムにニュースを取得
# 出力: tmp/news.txt (ニュース見出し+本文要約 3件)
# 過去に使った見出しは除外して同じニュースを繰り返さない

cd "$(dirname "$0")"
mkdir -p tmp

OUTFILE="tmp/news.txt"
PAST_NEWS="tmp/.past_news_titles.txt"
RSS_URL="https://news.yahoo.co.jp/rss/topics/top-picks.xml"

# RSS取得
xml=$(curl -s --max-time 10 "$RSS_URL" 2>/dev/null)
if [ -z "$xml" ]; then
    rm -f "$OUTFILE"
    exit 0
fi

# item の title と link を抽出
# 各itemから "title\tlink" のペアを作る
items=$(echo "$xml" | awk '
    /<item>/  { in_item=1; title=""; link="" }
    /<\/item>/ { if (in_item && title && link) print title "\t" link; in_item=0 }
    in_item && /<title>/ { gsub(/.*<title>/, ""); gsub(/<\/title>.*/, ""); title=$0 }
    in_item && /<link>/  { gsub(/.*<link>/, ""); gsub(/<\/link>.*/, ""); link=$0 }
')

if [ -z "$items" ]; then
    rm -f "$OUTFILE"
    exit 0
fi

# 過去に使った見出しを読み込み
past_titles=""
[ -f "$PAST_NEWS" ] && past_titles=$(cat "$PAST_NEWS")

# 過去に使ったものを除外
available=""
while IFS=$'\t' read -r title link; do
    [ -z "$title" ] && continue
    if ! echo "$past_titles" | grep -qF "$title"; then
        available="${available}${title}\t${link}\n"
    fi
done <<< "$items"

# 全部使い切ったらリセット
if [ -z "$available" ]; then
    available=""
    while IFS=$'\t' read -r title link; do
        [ -z "$title" ] && continue
        available="${available}${title}\t${link}\n"
    done <<< "$items"
    > "$PAST_NEWS"
fi

# シャッフルして3件選ぶ
selected=$(echo -e "$available" | grep -v '^$' | sort -R | head -3)

# 各ニュースの見出し＋本文要約を取得
result=""
while IFS=$'\t' read -r title link; do
    [ -z "$title" ] && continue

    # 使用済みとして記録
    echo "$title" >> "$PAST_NEWS"

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

# 過去記録は直近50件保持
tail -50 "$PAST_NEWS" > "${PAST_NEWS}.tmp" && mv "${PAST_NEWS}.tmp" "$PAST_NEWS"

if [ -n "$result" ]; then
    echo "$result" > "$OUTFILE"
else
    rm -f "$OUTFILE"
fi
