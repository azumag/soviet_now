#!/usr/bin/env python3
"""Google News マルチソース見出し取得スクリプト.

Google News RSS (日本政治/日本経済/日本国際/世界/US/ビジネス)
から見出しを取得し、tmp/google_headlines.txt に ■ プレフィックス形式で出力する。
既読管理ファイルで重複排除。

時事コーナーは先頭の未読見出しを採るため、ソースを巡回順に並べ、
芸能・週刊誌・下世話な事件・地方の小さな事件を出力前に落とす。

Usage:
    python3 lib/fetch_google_headlines.py
"""
import json
import os
import re
import sys
import html
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET

# --- sports filter ---
try:
    _lib_dir = os.path.dirname(__file__)
    if _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
    from sports_filter import is_sports_title  # type: ignore
except Exception:  # fallback
    def is_sports_title(title: str) -> bool:  # type: ignore
        return False

try:
    from news_topic_filter import (  # type: ignore
        is_low_value_news_title,
        is_public_affairs_beyond_incident_title,
        is_uncontextualized_tragedy,
    )
except Exception:  # fallback
    def is_low_value_news_title(title: str) -> bool:  # type: ignore
        return False

    def is_public_affairs_beyond_incident_title(title: str) -> bool:  # type: ignore
        return True

    def is_uncontextualized_tragedy(title: str, article_text: str = "") -> bool:  # type: ignore
        return False

RSS_URLS = {
    # 日本の総合トップ (topics/...MDNfM2Q) は地方の事件・生活情報・週刊誌記事が先頭を占め、
    # 先頭採用の時事コーナーが俗っぽくなるため使わない。日本政治を巡回の先頭に置く。
    # 科学/テクノロジー・ヘルス・中国総合はゲーム情報・雑学・生活記事・宣伝記事が多く、
    # 時事として扱える見出しがほぼ無いため使わない。
    "jp_politics":"https://news.google.com/rss/search?q=%E6%94%BF%E6%B2%BB+OR+%E5%9B%BD%E4%BC%9A+OR+%E9%A6%96%E7%9B%B8+OR+%E6%94%BF%E5%BA%9C+OR+%E9%81%B8%E6%8C%99&hl=ja&gl=JP&ceid=JP:ja",
    "jp_biz":     "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtcGhHZ0pLVUNnQVAB?hl=ja&gl=JP&ceid=JP%3Aja",
    "jp_wrld":    "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtcGhHZ0pLVUNnQVAB?hl=ja&gl=JP&ceid=JP%3Aja",
    "world":      "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
    "us":         "https://news.google.com/rss/topics/CAAqIggKIhxDQkFTRHdvSkwyMHZNRGxqTjNjd0VnSmxiaWdBUAE?hl=en-US&gl=US&ceid=US:en",
    "biz":        "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen",
}
DEFAULT_RSS = "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"
OUTPUT_FILE = "tmp/google_headlines.txt"
META_FILE = "tmp/google_headlines_meta.json"
PAST_TITLES_FILE = "tmp/history/.past_jiji_titles.txt"
PAST_KEYS_FILE = "tmp/history/.past_jiji_keys.txt"
USER_AGENT = "soren-radio-grounding/1.0"
MAX_HEADLINES = 50
MAX_PER_FEED = 8
# 政党・官邸の自前ページは報道ではなく一次資料なので、党派を問わず時事の題材にしない。
NON_NEWS_SOURCES = frozenset((
    "首相官邸", "衆議院トップページ", "参議院", "衆議院",
    "自由民主党", "立憲民主党", "公明党", "日本維新の会", "国民民主党", "新・国民民主党",
    "日本共産党", "れいわ新選組", "参政党", "社会民主党", "日本保守党", "チームみらい",
))


def http_get(url: str, timeout: float = 10.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_key(s: str) -> str:
    """Normalize title to dedup key (same logic as news_filter.py)."""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] not in ("P", "S"))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]


def load_past_keys() -> set:
    keys = set()
    for path in (PAST_TITLES_FILE, PAST_KEYS_FILE):
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    t = line.strip()
                    if t:
                        keys.add(title_key(t) if path == PAST_TITLES_FILE else t)
    return keys


def is_jiji_eligible_title(title: str) -> bool:
    """Return True for headlines fit for the jiji (public affairs) corner."""
    source = re.search(r"\s[-–—]\s([^-–—]{1,80})$", title)
    if source and source.group(1).strip() in NON_NEWS_SOURCES:
        return False
    return (
        not is_sports_title(title)
        and not is_low_value_news_title(title)
        and is_public_affairs_beyond_incident_title(title)
        and not is_uncontextualized_tragedy(title)
    )


def fetch_headlines() -> list[tuple[str, str]]:
    """Return eligible (title, url) pairs, interleaved across feeds."""
    per_feed = []
    for feed_url in RSS_URLS.values():
        feed_items = []
        try:
            raw = http_get(feed_url)
            root = ET.fromstring(raw)
            for item in root.findall("./channel/item"):
                t = strip_tags(item.findtext("title", default=""))
                link = (item.findtext("link", default="") or "").strip()
                if t and is_jiji_eligible_title(t):
                    feed_items.append((t, link))
                if len(feed_items) >= MAX_PER_FEED:
                    break
        except Exception as e:
            print(f"  [fetch_google_headlines] feed failed, continuing: {e}", file=sys.stderr)
        per_feed.append(feed_items)
    # 1フィードが先頭を独占しないよう、各フィードから1件ずつ巡回して並べる。
    items = []
    for rank in range(MAX_PER_FEED):
        for feed_items in per_feed:
            if rank < len(feed_items):
                items.append(feed_items[rank])
    return items[:MAX_HEADLINES]


def main():
    try:
        items = fetch_headlines()
    except Exception as e:
        print(f"Error fetching Google News RSS: {e}", file=sys.stderr)
        return 1

    if not items:
        print("No headlines found", file=sys.stderr)
        return 1

    past_keys = load_past_keys()

    lines = []
    meta = {}
    for t, url in items:
        if url:
            meta[t] = {"url": url}
        k = title_key(t)
        if k and k not in past_keys:
            lines.append(f"\u25a0 {t}")

    # Even if all are read, output all eligible titles (caller handles empty unread)
    if not lines:
        lines = [f"\u25a0 {t}" for t, _url in items]

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # メタ情報 (URL等) をJSON出力 — jiji フィルタの URL hash 重複排除に使用
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    print(f"Wrote {len(lines)} headlines to {OUTPUT_FILE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
