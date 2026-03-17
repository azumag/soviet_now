#!/usr/bin/env python3
"""News filtering utilities: title_key, filter_unread, resolve_title.

Usage:
    python3 news_filter.py title_key <title>
    python3 news_filter.py filter_unread <past_read_file> <past_keys_file> <news_file> [past_url_hash_file] [meta_file]
    python3 news_filter.py resolve_title <selected_title> <news_file>
"""
import hashlib
import json
import os
import re
import sys
import unicodedata


def key(s: str) -> str:
    """Normalize a news title to a dedup key."""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]


def url_hash(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def cmd_title_key():
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    print(key(title))


def cmd_filter_unread():
    past_title_file = sys.argv[2]
    past_key_file = sys.argv[3]
    news_file = sys.argv[4]
    past_url_hash_file = sys.argv[5] if len(sys.argv) > 5 else ""
    meta_file = sys.argv[6] if len(sys.argv) > 6 else ""

    news_text = ""
    if os.path.exists(news_file):
        with open(news_file, encoding="utf-8", errors="ignore") as f:
            news_text = f.read()
    meta = {}
    if meta_file and os.path.exists(meta_file):
        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    past_keys = set()
    if os.path.exists(past_title_file):
        for ln in open(past_title_file, encoding="utf-8", errors="ignore"):
            t = ln.strip()
            if not t:
                continue
            k = key(t)
            if k:
                past_keys.add(k)
    if os.path.exists(past_key_file):
        for ln in open(past_key_file, encoding="utf-8", errors="ignore"):
            k = ln.strip()
            if k:
                past_keys.add(k)

    past_url_hashes = set()
    if past_url_hash_file and os.path.exists(past_url_hash_file):
        for ln in open(past_url_hash_file, encoding="utf-8", errors="ignore"):
            k = ln.strip()
            if k:
                past_url_hashes.add(k)

    blocks = []
    current = []
    for line in news_text.splitlines():
        if line.startswith("\u25a0 "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    seen = set()
    seen_url_hashes = set()
    out = []
    for b in blocks:
        title = b[0][2:].strip()
        k = key(title)
        item = meta.get(title, {}) if isinstance(meta, dict) else {}
        uh = url_hash(item.get("url", ""))
        if not k:
            continue
        if k in seen:
            continue
        if uh and uh in seen_url_hashes:
            continue
        if k in past_keys:
            continue
        if uh and uh in past_url_hashes:
            continue
        seen.add(k)
        if uh:
            seen_url_hashes.add(uh)
        out.append("\n".join(b).rstrip())

    print("\n\n".join(out))


def cmd_resolve_title():
    selected = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
    news_file = sys.argv[3] if len(sys.argv) > 3 else ""

    titles = []
    if os.path.exists(news_file):
        with open(news_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("\u25a0 "):
                    titles.append(line[2:].strip())

    if not selected:
        print("")
        raise SystemExit(0)
    if not titles:
        print(selected)
        raise SystemExit(0)

    sel_key = key(selected)
    for t in titles:
        if t.strip() == selected:
            print(t)
            raise SystemExit(0)
    for t in titles:
        if key(t) == sel_key and sel_key:
            print(t)
            raise SystemExit(0)
    for t in titles:
        tk = key(t)
        if sel_key and (sel_key in tk or tk in sel_key):
            print(t)
            raise SystemExit(0)

    print(selected)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: news_filter.py {title_key|filter_unread|resolve_title} ...", file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    if subcmd == "title_key":
        cmd_title_key()
    elif subcmd == "filter_unread":
        cmd_filter_unread()
    elif subcmd == "resolve_title":
        cmd_resolve_title()
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)
