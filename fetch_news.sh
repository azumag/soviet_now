#!/bin/bash
# fetch_news.sh - Yahoo News RSS 複数カテゴリからランダムにニュースを取得
# 出力: tmp/news.txt (ニュース見出し+本文要約 3件)
# 過去に使った見出しは除外して同じニュースを繰り返さない

cd "$(dirname "$0")"
mkdir -p tmp

OUTFILE="tmp/news.txt"
PAST_NEWS="tmp/.past_news_titles.txt"
PAST_NEWS_LINKS="tmp/.past_news_links.txt"
LAST_NEWS_CACHE="tmp/.news_last_success.txt"

# 複数カテゴリの RSS を使い候補を増やす
RSS_URLS=(
    "https://news.yahoo.co.jp/rss/topics/top-picks.xml"
    "https://news.yahoo.co.jp/rss/topics/domestic.xml"
    "https://news.yahoo.co.jp/rss/topics/world.xml"
    "https://news.yahoo.co.jp/rss/topics/politics.xml"
    "https://news.yahoo.co.jp/rss/topics/economy.xml"
    "https://news.yahoo.co.jp/rss/topics/business.xml"
    "https://news.yahoo.co.jp/rss/topics/it.xml"
    "https://news.yahoo.co.jp/rss/topics/science.xml"
    "https://news.yahoo.co.jp/rss/topics/local.xml"
    "https://news.yahoo.co.jp/rss/topics/life.xml"
)
RSS_PICK_COUNT=5

# ランダムに複数カテゴリ選ぶ（毎回違うジャンルから取得）
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

# 先頭 N 件を選択
pick_count="$RSS_PICK_COUNT"
total_count="${#indices[@]}"
if [ "$pick_count" -gt "$total_count" ]; then
    pick_count="$total_count"
fi
for ((k=0; k<pick_count; k++)); do
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
    # RSS取得失敗時は直近成功キャッシュを再利用
    if [ -s "$LAST_NEWS_CACHE" ]; then
        cp "$LAST_NEWS_CACHE" "$OUTFILE"
    elif [ -s "$PAST_NEWS" ]; then
        # キャッシュもない場合は過去見出しを再掲して無音を避ける
        tail -3 "$PAST_NEWS" | awk 'NF {print "■ " $0 "\n"}' > "$OUTFILE"
    else
        rm -f "$OUTFILE"
    fi
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
    # 全件既読で枯渇した場合は履歴をリセットして再抽出
    : > "$PAST_NEWS"
    : > "$PAST_NEWS_LINKS"

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
        printf '%s\t%s\n' "$title" "$link" >>"$available_file"
        seen_titles="${seen_titles}${title}"$'\n'
        seen_links="${seen_links}${link}"$'\n'
    done <<< "$all_items"
fi

if [ ! -s "$available_file" ]; then
    # それでも候補ゼロならキャッシュを利用
    rm -f "$available_file"
    if [ -s "$LAST_NEWS_CACHE" ]; then
        cp "$LAST_NEWS_CACHE" "$OUTFILE"
    elif [ -s "$PAST_NEWS" ]; then
        tail -3 "$PAST_NEWS" | awk 'NF {print "■ " $0 "\n"}' > "$OUTFILE"
    else
        rm -f "$OUTFILE"
    fi
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
    cp "$OUTFILE" "$LAST_NEWS_CACHE"
else
    if [ -s "$LAST_NEWS_CACHE" ]; then
        cp "$LAST_NEWS_CACHE" "$OUTFILE"
    elif [ -s "$PAST_NEWS" ]; then
        tail -3 "$PAST_NEWS" | awk 'NF {print "■ " $0 "\n"}' > "$OUTFILE"
    else
        rm -f "$OUTFILE"
    fi
fi
