#!/usr/bin/env python3
"""Google News トップ見出し取得スクリプト.

Google News RSS (日本語) からトップ見出しを取得し、
tmp/google_headlines.txt に ■ プレフィックス形式で出力する。
既読管理ファイルで重複排除。

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

RSS_URL = "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"
OUTPUT_FILE = "tmp/google_headlines.txt"
META_FILE = "tmp/google_headlines_meta.json"
PAST_TITLES_FILE = "tmp/history/.past_jiji_titles.txt"
PAST_KEYS_FILE = "tmp/history/.past_jiji_keys.txt"
USER_AGENT = "soren-radio-grounding/1.0"
MAX_HEADLINES = 50


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


def fetch_headlines() -> list[tuple[str, str]]:
    """Return list of (title, url) from Google News RSS."""
    raw = http_get(RSS_URL)
    root = ET.fromstring(raw)
    items = []
    for item in root.findall("./channel/item"):
        t = strip_tags(item.findtext("title", default=""))
        link = (item.findtext("link", default="") or "").strip()
        if t:
            items.append((t, link))
        if len(items) >= MAX_HEADLINES:
            break
    return items


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

    # Even if all are read, output all titles (caller handles empty unread)
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
