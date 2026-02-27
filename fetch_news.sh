#!/bin/bash
# fetch_news.sh - Yahoo News RSSからランダムにニュースを取得
# 出力: tmp/news.txt (ニュース見出し3件)

cd "$(dirname "$0")"
mkdir -p tmp

OUTFILE="tmp/news.txt"
RSS_URL="https://news.yahoo.co.jp/rss/topics/top-picks.xml"

# RSS取得
xml=$(curl -s --max-time 10 "$RSS_URL" 2>/dev/null)
if [ -z "$xml" ]; then
    rm -f "$OUTFILE"
    exit 0
fi

# titleタグを抽出（最初の1つはチャンネルタイトルなのでスキップ）
headlines=$(echo "$xml" | grep '<title>' | sed 's/.*<title>//;s/<\/title>.*//' | tail -n +2)

if [ -z "$headlines" ]; then
    rm -f "$OUTFILE"
    exit 0
fi

# シャッフルして3件選ぶ
selected=$(echo "$headlines" | sort -R | head -3)

echo "$selected" > "$OUTFILE"
