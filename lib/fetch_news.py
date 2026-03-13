#!/usr/bin/env python3
"""Fetch news from public RSS sources and emit backward-compatible output."""

from __future__ import annotations

import html
import json
import os
import random
import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
TMP_DIR = os.path.join(ROOT_DIR, "tmp")
TMP_HISTORY_DIR = os.path.join(TMP_DIR, "history")
TMP_STATE_DIR = os.path.join(TMP_DIR, "state")

OUTFILE = os.path.join(TMP_DIR, "news.txt")
META_OUTFILE = os.path.join(TMP_DIR, "news_meta.json")
PAST_NEWS = os.path.join(TMP_HISTORY_DIR, ".past_news_titles.txt")
PAST_NEWS_LINKS = os.path.join(TMP_HISTORY_DIR, ".past_news_links.txt")
LAST_NEWS_CACHE = os.path.join(TMP_STATE_DIR, ".news_last_success.txt")
LAST_NEWS_META_CACHE = os.path.join(TMP_STATE_DIR, ".news_last_success_meta.json")
NEWS_ALLOW_STALE_CACHE = os.environ.get("NEWS_ALLOW_STALE_CACHE", "0")

OUTPUT_COUNT = 3
SUMMARY_LIMIT = 4000
REQUEST_TIMEOUT = 8.0
USER_AGENT = "soren-news-fetcher/1.0"

DISABLED_SOURCE_NAMES = {"首相官邸", "Kantei", "kantei"}

SOURCES = [
    {
        "url": "https://ja.wikinews.org/w/index.php?title=特別:新しいページ&feed=rss",
        "key": "wikinews",
        "name": "ウィキニュース",
        "license": "CC BY 4.0",
        "lang": "ja",
    },
    {
        "url": "https://en.wikinews.org/w/index.php?title=Special:NewPages&feed=rss",
        "key": "wikinews_en",
        "name": "Wikinews(EN)",
        "license": "CC BY 4.0",
        "lang": "en",
    },
    {
        "url": "https://fr.wikinews.org/w/index.php?title=Spécial:Nouvelles_pages&feed=rss",
        "key": "wikinews_fr",
        "name": "Wikinews(FR)",
        "license": "CC BY 4.0",
        "lang": "fr",
    },
    {
        "url": "https://ru.wikinews.org/w/index.php?title=Служебная:Новые_страницы&feed=rss",
        "key": "wikinews_ru",
        "name": "Wikinews(RU)",
        "license": "CC BY 4.0",
        "lang": "ru",
    },
    {
        "url": "https://de.wikinews.org/w/index.php?title=Spezial:Neue_Seiten&feed=rss",
        "key": "wikinews_de",
        "name": "Wikinews(DE)",
        "license": "CC BY 4.0",
        "lang": "de",
    },
    {
        "url": "https://ar.wikinews.org/w/index.php?title=خاص:صفحات_جديدة&feed=rss",
        "key": "wikinews_ar",
        "name": "Wikinews(AR)",
        "license": "CC BY 4.0",
        "lang": "ar",
    },
    {
        "url": "https://cs.wikinews.org/w/index.php?title=Speciální:Nové_stránky&feed=rss",
        "key": "wikinews_cs",
        "name": "Wikinews(CS)",
        "license": "CC BY 4.0",
        "lang": "cs",
    },
    {
        "url": "https://eo.wikinews.org/w/index.php?title=Specialaĵo:Novaj_paĝoj&feed=rss",
        "key": "wikinews_eo",
        "name": "Wikinews(EO)",
        "license": "CC BY 4.0",
        "lang": "eo",
    },
    {
        "url": "https://fi.wikinews.org/w/index.php?title=Toiminnot:Uudet_sivut&feed=rss",
        "key": "wikinews_fi",
        "name": "Wikinews(FI)",
        "license": "CC BY 4.0",
        "lang": "fi",
    },
    {
        "url": "https://he.wikinews.org/w/index.php?title=מיוחד:דפים_חדשים&feed=rss",
        "key": "wikinews_he",
        "name": "Wikinews(HE)",
        "license": "CC BY 4.0",
        "lang": "he",
    },
    {
        "url": "https://pl.wikinews.org/w/index.php?title=Specjalna:Nowe_strony&feed=rss",
        "key": "wikinews_pl",
        "name": "Wikinews(PL)",
        "license": "CC BY 4.0",
        "lang": "pl",
    },
    {
        "url": "https://uk.wikinews.org/w/index.php?title=Спеціальна:Нові_сторінки&feed=rss",
        "key": "wikinews_uk",
        "name": "Wikinews(UK)",
        "license": "CC BY 4.0",
        "lang": "uk",
    },
    {
        "url": "https://zh.wikinews.org/w/index.php?title=Special:新页面&feed=rss",
        "key": "wikinews_zh",
        "name": "Wikinews(ZH)",
        "license": "CC BY 4.0",
        "lang": "zh",
    },
    {
        "url": "https://jp.globalvoices.org/feed/",
        "key": "globalvoices",
        "name": "Global Voices",
        "license": "CC BY 3.0",
        "lang": "ja",
    },
    {
        "url": "https://globalvoices.org/feed/",
        "key": "globalvoices_en",
        "name": "Global Voices(EN)",
        "license": "CC BY 3.0",
        "lang": "en",
    },
    {
        "url": "https://fr.globalvoices.org/feed/",
        "key": "globalvoices_fr",
        "name": "Global Voices(FR)",
        "license": "CC BY 3.0",
        "lang": "fr",
    },
    {
        "url": "https://ru.globalvoices.org/feed/",
        "key": "globalvoices_ru",
        "name": "Global Voices(RU)",
        "license": "CC BY 3.0",
        "lang": "ru",
    },
    {
        "url": "https://es.globalvoices.org/feed/",
        "key": "globalvoices_es",
        "name": "Global Voices(ES)",
        "license": "CC BY 3.0",
        "lang": "es",
    },
    {
        "url": "https://ar.globalvoices.org/feed/",
        "key": "globalvoices_ar",
        "name": "Global Voices(AR)",
        "license": "CC BY 3.0",
        "lang": "ar",
    },
    {
        "url": "https://de.globalvoices.org/feed/",
        "key": "globalvoices_de",
        "name": "Global Voices(DE)",
        "license": "CC BY 3.0",
        "lang": "de",
    },
    {
        "url": "https://pt.globalvoices.org/feed/",
        "key": "globalvoices_pt",
        "name": "Global Voices(PT)",
        "license": "CC BY 3.0",
        "lang": "pt",
    },
    {
        "url": "https://zhs.globalvoices.org/feed/",
        "key": "globalvoices_zh",
        "name": "Global Voices(ZH)",
        "license": "CC BY 3.0",
        "lang": "zh",
    },
]


def source_family(key: str) -> str:
    """Group wikinews/globalvoices variants together for balanced selection."""
    if key.startswith("wikinews"):
        return "wikinews"
    if key.startswith("globalvoices"):
        return "globalvoices"
    return key


def ensure_dirs() -> None:
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(TMP_HISTORY_DIR, exist_ok=True)
    os.makedirs(TMP_STATE_DIR, exist_ok=True)


def http_get(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-._~%")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_tags(text: str) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return collapse_ws(text)


def trim_summary(text: str, limit: int = SUMMARY_LIMIT) -> str:
    text = collapse_ws(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def title_key(title: str) -> str:
    s = unicodedata.normalize("NFKC", title or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] not in ("P", "S"))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]


def safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def write_text(path: str, text: str) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_path, path)


def write_json(path: str, data) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def load_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except OSError:
        return []


def append_and_trim(path: str, values: list[str], max_lines: int) -> None:
    lines = load_lines(path)
    lines.extend(v for v in values if v)
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    if lines:
        write_text(path, "\n".join(lines) + "\n")
    else:
        safe_unlink(path)


def load_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def filter_disabled_cached_outputs(news_text: str, meta: dict) -> tuple[str, dict]:
    filtered_meta = {
        title: item
        for title, item in meta.items()
        if collapse_ws((item or {}).get("source", "")) not in DISABLED_SOURCE_NAMES
    }
    if len(filtered_meta) == len(meta):
        return news_text, filtered_meta

    blocks = []
    current = []
    for raw_line in news_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("■ "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    filtered_blocks = []
    for block in blocks:
        title = block[0][2:].strip() if block and block[0].startswith("■ ") else ""
        if title in filtered_meta:
            filtered_blocks.append("\n".join(block).rstrip())

    filtered_text = "\n\n".join(filtered_blocks).strip()
    if filtered_text:
        filtered_text += "\n"
    return filtered_text, filtered_meta


def restore_stale_cache() -> bool:
    if NEWS_ALLOW_STALE_CACHE != "1":
        return False
    news_text = load_text(LAST_NEWS_CACHE)
    meta = load_json(LAST_NEWS_META_CACHE)
    if not news_text or not isinstance(meta, dict):
        return False
    news_text, meta = filter_disabled_cached_outputs(news_text, meta)
    if not news_text or not meta:
        return False
    write_text(OUTFILE, news_text)
    write_json(META_OUTFILE, meta)
    return True


def clear_outputs() -> None:
    safe_unlink(OUTFILE)
    safe_unlink(META_OUTFILE)


def localname(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def child_text(item: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(item):
        if localname(child.tag) in wanted:
            return collapse_ws("".join(child.itertext()))
    return ""


def child_attr(item: ET.Element, attr_name: str) -> str:
    for key, value in item.attrib.items():
        if localname(key) == attr_name:
            return collapse_ws(value)
    return ""


def extract_link(item: ET.Element) -> str:
    for child in list(item):
        if localname(child.tag) != "link":
            continue
        text = collapse_ws("".join(child.itertext()))
        if text:
            return text
        for key, value in child.attrib.items():
            if localname(key) in {"resource", "href"}:
                return collapse_ws(value)
    return child_attr(item, "about")


def iter_items(root: ET.Element):
    for elem in root.iter():
        if localname(elem.tag) == "item":
            yield elem


def strip_wikitext(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"\[(https?://[^\s\]]+)\s+([^\]]+)\]", r"\2", text)
    for _ in range(4):
        new_text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if new_text == text:
            break
        text = new_text
    text = re.sub(r"\[\[([^|\]]*\|)?([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"''+", "", text)
    text = strip_tags(text)
    return trim_summary(text)


def clean_item(source: dict, item: ET.Element) -> dict | None:
    title = strip_tags(child_text(item, "title"))
    url = extract_link(item)
    if not title or not url:
        return None

    raw_description = child_text(item, "description")
    if not raw_description:
        raw_description = child_text(item, "encoded")

    if source["key"].startswith("wikinews"):
        summary = strip_wikitext(raw_description)
    else:
        summary = trim_summary(strip_tags(raw_description))

    author = strip_tags(child_text(item, "creator", "author"))

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "source": source["name"],
        "author": author,
        "license": source["license"],
        "lang": source.get("lang", "ja"),
        "source_key": source["key"],
    }


def fetch_source_items(source: dict) -> list[dict]:
    try:
        raw = http_get(source["url"])
    except Exception:
        return []
    if not raw.strip():
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    items = []
    for item in iter_items(root):
        cleaned = clean_item(source, item)
        if cleaned:
            items.append(cleaned)
    return items


def dedupe_candidates(all_source_items: dict[str, list[dict]]) -> dict[str, list[dict]]:
    past_title_keys = {title_key(title) for title in load_lines(PAST_NEWS)}
    past_links = set(load_lines(PAST_NEWS_LINKS))
    seen_title_keys: set[str] = set()
    seen_links: set[str] = set()
    filtered: dict[str, list[dict]] = {}

    for source in SOURCES:
        key = source["key"]
        source_items = []
        for item in all_source_items.get(key, []):
            item_title_key = title_key(item["title"])
            item_link = item["url"]
            if not item_title_key or not item_link:
                continue
            if item_title_key in past_title_keys or item_link in past_links:
                continue
            if item_title_key in seen_title_keys or item_link in seen_links:
                continue
            seen_title_keys.add(item_title_key)
            seen_links.add(item_link)
            source_items.append(item)
        if source_items:
            filtered[key] = source_items
    return filtered


def pick_articles(candidates: dict[str, list[dict]]) -> list[dict]:
    source_order = [source["key"] for source in SOURCES if candidates.get(source["key"])]
    random.shuffle(source_order)

    selected = []
    offsets = {key: 0 for key in source_order}
    family_used: dict[str, int] = {}

    # First pass: pick at most 1 from each family for diversity
    for key in source_order:
        fam = source_family(key)
        if family_used.get(fam, 0) >= 1:
            continue
        items = candidates.get(key, [])
        if not items:
            continue
        selected.append(items[0])
        offsets[key] = 1
        family_used[fam] = family_used.get(fam, 0) + 1
        if len(selected) >= OUTPUT_COUNT:
            return selected

    # Second pass: fill remaining from any source
    while len(selected) < OUTPUT_COUNT:
        progressed = False
        for key in source_order:
            index = offsets.get(key, 0)
            items = candidates.get(key, [])
            if index >= len(items):
                continue
            selected.append(items[index])
            offsets[key] = index + 1
            progressed = True
            if len(selected) >= OUTPUT_COUNT:
                break
        if not progressed:
            break

    return selected


def render_news(selected: list[dict]) -> str:
    blocks = []
    for item in selected:
        block = [f"■ {item['title']}"]
        if item["summary"]:
            block.append(item["summary"])
        blocks.append("\n".join(block))
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def render_meta(selected: list[dict]) -> dict[str, dict]:
    meta = {}
    for item in selected:
        meta[item["title"]] = {
            "source": item["source"],
            "author": item["author"],
            "url": item["url"],
            "license": item["license"],
            "lang": item.get("lang", "ja"),
        }
    return meta


def main() -> int:
    ensure_dirs()

    all_source_items = {source["key"]: fetch_source_items(source) for source in SOURCES}
    fetched_any = any(all_source_items.values())

    if not fetched_any:
        if not restore_stale_cache():
            clear_outputs()
        return 0

    candidates = dedupe_candidates(all_source_items)
    selected = pick_articles(candidates)
    if not selected:
        clear_outputs()
        return 0

    news_text = render_news(selected)
    meta = render_meta(selected)

    if not news_text:
        clear_outputs()
        return 0

    write_text(OUTFILE, news_text)
    write_json(META_OUTFILE, meta)
    write_text(LAST_NEWS_CACHE, news_text)
    write_json(LAST_NEWS_META_CACHE, meta)

    append_and_trim(PAST_NEWS, [item["title"] for item in selected], 120)
    append_and_trim(PAST_NEWS_LINKS, [item["url"] for item in selected], 200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
