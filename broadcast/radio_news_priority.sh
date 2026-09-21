# broadcast/radio_news_priority.sh - 時事ニュースの政治優先選定
# radio_news.sh の既存選定関数を、政治・政策・外交・安全保障を約2/3で
# 優先する二段階選定に差し替える。選んだレーン内では従来の鮮度・媒体分散を維持する。

_random_pick_news_block() {
	local blocks_text="$1"
	local root="${ELOOP_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
	python3 "$root/lib/news_priority.py" "$PAST_NEWS_READ_SOURCES" "$blocks_text" "${PAST_NEWS_READ:-}"
}
