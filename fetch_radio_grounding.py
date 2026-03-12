#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


USER_AGENT = "soren-radio-grounding/1.0"


def http_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_query(query: str) -> str:
    q = query.strip()
    q = q.replace("を深掘りして", "")
    q = q.replace("を深掘り", "")
    q = q.replace("深掘りして", "")
    q = q.replace("深掘り", "")
    q = q.replace("話。", " ")
    q = q.replace("の話", " ")
    q = q.replace("話 ", " ")
    q = re.sub(r"[()（）「」『』]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def load_cache(cache_file: str, ttl_sec: int) -> str:
    if not os.path.exists(cache_file):
        return ""
    age = time.time() - os.path.getmtime(cache_file)
    if age > ttl_sec:
        return ""
    try:
        with open(cache_file, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_cache(cache_file: str, text: str) -> None:
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    tmp_file = cache_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_file, cache_file)


def wikipedia_search(query: str, lang: str, limit: int):
    params = {
        "action": "query",
        "list": "search",
        "format": "json",
        "utf8": "1",
        "srlimit": str(limit),
        "srsearch": query,
    }
    url = f"https://{lang}.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}"
    raw = http_get(url)
    data = json.loads(raw)
    return data.get("query", {}).get("search", [])


def wikipedia_summary(title: str, lang: str):
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    raw = http_get(url)
    data = json.loads(raw)
    summary = strip_tags(data.get("extract", ""))
    if not summary:
        return None
    page_url = (
        data.get("content_urls", {})
        .get("desktop", {})
        .get("page", f"https://{lang}.wikipedia.org/wiki/{encoded}")
    )
    return {
        "label": f"Wikipedia {lang.upper()}",
        "title": strip_tags(data.get("title", title)),
        "url": page_url,
        "summary": summary[:900],
    }


def google_news_search(query: str, limit: int):
    params = {"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    url = f"https://news.google.com/rss/search?{urllib.parse.urlencode(params)}"
    raw = http_get(url)
    root = ET.fromstring(raw)
    items = []
    for item in root.findall("./channel/item"):
        title = strip_tags(item.findtext("title", default=""))
        link = item.findtext("link", default="").strip()
        desc = strip_tags(item.findtext("description", default=""))
        pub_date = strip_tags(item.findtext("pubDate", default=""))
        if not title or not link:
            continue
        items.append(
            {
                "label": "Google News",
                "title": title,
                "url": link,
                "summary": desc[:700],
                "date": pub_date,
            }
        )
        if len(items) >= limit:
            break
    return items


def collect_sources(corner: str, query: str, max_sources: int):
    sources = []
    seen_urls = set()

    def add_source(src):
        if not src:
            return
        url = src.get("url", "")
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        sources.append(src)

    wiki_limit = max_sources if not sources else max(1, max_sources - len(sources))
    for lang in ("ja", "en"):
        if len(sources) >= max_sources:
            break
        try:
            search_results = wikipedia_search(query, lang, wiki_limit)
        except Exception:
            continue
        for row in search_results:
            if len(sources) >= max_sources:
                break
            title = row.get("title", "").strip()
            if not title:
                continue
            try:
                add_source(wikipedia_summary(title, lang))
            except Exception:
                continue

    return sources[:max_sources]


def render_output(query: str, sources) -> str:
    if not sources:
        return ""
    lines = [f"Query: {query}", ""]
    for src in sources:
        lines.append(f"[{src.get('label', 'Source')}] {src.get('title', '')}")
        if src.get("date"):
            lines.append(f"Date: {src['date']}")
        lines.append(f"URL: {src.get('url', '')}")
        lines.append(f"Summary: {src.get('summary', '')}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corner", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--ttl-sec", type=int, default=21600)
    parser.add_argument("--max-sources", type=int, default=3)
    parser.add_argument("--cache-dir", default="tmp/.radio_grounding_cache")
    args = parser.parse_args()

    query = normalize_query(args.query)
    if not query:
        return 0

    cache_key = hashlib.sha256(
        json.dumps(
            {"corner": args.corner, "query": query, "max_sources": args.max_sources},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    cache_file = os.path.join(args.cache_dir, f"{cache_key}.txt")

    cached = load_cache(cache_file, args.ttl_sec)
    if cached:
        sys.stdout.write(cached)
        return 0

    sources = collect_sources(args.corner, query, max(1, args.max_sources))
    output = render_output(query, sources)
    if output:
        save_cache(cache_file, output)
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
