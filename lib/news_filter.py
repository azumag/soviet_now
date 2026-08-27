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

# --- sports filter ---
try:
    _lib_dir = os.path.dirname(__file__)
    if _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
    from sports_filter import is_sports_title  # type: ignore
except Exception:  # fallback
    def is_sports_title(title: str) -> bool:  # type: ignore
        return False


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


# --- 同一事件の別見出し検出 -------------------------------------------------
# Google News は同じ事件を媒体ごとに違う見出しで並べる。タイトル一致でも URL 一致でも
# ないため従来の既読判定を素通りし、同じ事件を何度も読み上げていた
# (2026-08-26 実例: パキスタン病院火災を Reuters / Al Jazeera / NYT の見出しで 3 回)。
# 見出しから内容語を取り出し、既読見出しと十分に重なるものを同一事件として弾く。

# 末尾の " - 媒体名" を落とす (媒体名は事件の同一性と無関係で、共通語になりやすい)
_OUTLET_SUFFIX_RE = re.compile(r"\s+[-\u2013\u2014|]\s+[^-\u2013\u2014|]{1,50}$")

_EVENT_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "but", "not",
    "are", "was", "were", "has", "had", "have", "will", "would", "could", "should", "can",
    "its", "his", "her", "their", "our", "your", "who", "what", "when", "where", "why", "how",
    "after", "before", "over", "under", "into", "onto", "out", "off", "than", "then", "there",
    "here", "more", "most", "less", "least", "new", "old", "amid", "ahead", "again", "still",
    "says", "say", "said", "reports", "report", "told", "tells", "live", "updates", "update",
    "exclusive", "breaking", "analysis", "opinion", "video", "photos", "watch", "read",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "about", "across", "against", "among", "around", "because", "between", "during",
    "some", "such", "only", "also", "just", "very", "may", "might", "must", "now", "get",
    "day", "days", "week", "weeks", "year", "years", "hour", "hours",
}


def event_tokens(title: str) -> set:
    """見出しから同一事件判定用の内容語を取り出す。"""
    s = _OUTLET_SUFFIX_RE.sub("", title or "")
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("\u2019", "'")
    s = re.sub(r"'s\b", "", s)
    tokens = set()
    for w in re.findall(r"[a-z]+", s):
        if len(w) >= 3 and w not in _EVENT_STOPWORDS:
            tokens.add(w)
    # 日本語見出し: カタカナ列・漢字列を 2 文字以上で拾う
    for w in re.findall(r"[\u30a1-\u30f6\u30fc]{2,}|[\u4e00-\u9fff]{2,}", s):
        tokens.add(w)
    return tokens


def generic_tokens(titles, ratio: float = 0.10, min_corpus: int = 80) -> frozenset:
    """コーパス全体に頻出する語 (ukraine / iran 等) を割り出す。

    こういう語は「その時期の一般的な話題」であって事件の同一性を示さない。
    実例: 「Vatican foreign minister, in Moscow, says Ukraine war must end」と
    「Ukraine needs changes to avoid losing war, ousted defence minister says」は
    {minister, ukraine, war} を共有するだけの別事件で、除外しないと誤検出する。

    コーパスは「既読履歴 + 今回の候補見出し」を渡すこと。候補見出しだけで数えると、
    同じ事件の別媒体見出しが 3〜4 本並んだときにその事件固有の語 (hospital / pakistan)
    まで頻出語に見えてしまい、狙った重複がすり抜ける。
    小さすぎるコーパスでは頻出語と固有語を区別できない (一つの事件の別媒体見出しが
    数本並ぶだけで、その事件固有の語が閾値を超えてしまう) ため、判定自体を行わない。
    本番の呼び出しは既読 60 件 + 候補 40 件程度で、常にこの下限を超える。
    """
    corpus = [t for t in titles if t]
    if len(corpus) < min_corpus:
        return frozenset()
    df: dict = {}
    for title in corpus:
        for token in event_tokens(title):
            df[token] = df.get(token, 0) + 1
    threshold = max(4, -(-len(corpus) * int(ratio * 100) // 100))
    return frozenset(t for t, c in df.items() if c >= threshold)


def same_event(a: set, b: set, generic: frozenset = frozenset()) -> bool:
    """内容語の重なりで同一事件かを判定する。"""
    if generic:
        a = a - generic
        b = b - generic
    if not a or not b:
        return False
    try:
        min_overlap = max(2, int(os.environ.get("NEWS_EVENT_OVERLAP_MIN", "3")))
    except ValueError:
        min_overlap = 3
    overlap = len(a & b)
    if overlap >= min_overlap:
        return True
    # 語数の少ない見出し同士は 2 語でも実質同一になりうる
    return overlap >= 2 and overlap >= 0.6 * min(len(a), len(b))


def event_dedup_enabled() -> bool:
    return os.environ.get("NEWS_EVENT_DEDUP", "1") != "0"


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

    # 既読見出しの内容語集合 (直近分のみ。古すぎる事件まで弾くと候補が枯れる)
    past_event_tokens = []
    past_titles_for_events: list = []
    if event_dedup_enabled() and os.path.exists(past_title_file):
        try:
            recent_limit = max(1, int(os.environ.get("NEWS_EVENT_HISTORY_LIMIT", "60")))
        except ValueError:
            recent_limit = 60
        past_titles_for_events = [
            ln.strip()
            for ln in open(past_title_file, encoding="utf-8", errors="ignore")
            if ln.strip()
        ][-recent_limit:]
        past_event_tokens = [
            t for t in (event_tokens(t) for t in past_titles_for_events) if t
        ]

    # 頻出語の判定は「既読履歴 + 今回の候補」をまとめたコーパスで行う
    generic = frozenset()
    if event_dedup_enabled():
        generic = generic_tokens(
            past_titles_for_events + [blk[0][2:].strip() for blk in blocks]
        )

    seen = set()
    seen_url_hashes = set()
    seen_event_tokens = []
    out = []
    for b in blocks:
        title = b[0][2:].strip()
        # sports filter: exclude baseball/sports topics per user request
        if is_sports_title(title):
            continue
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
        tokens = event_tokens(title) if event_dedup_enabled() else set()
        if tokens:
            # 採用・不採用にかかわらず記録する。同じ事件でも言い回しが離れた見出し同士
            # (「Islamabad hospital fire」と「Hospital Fire in Pakistan's Capital」) は
            # 直接は結び付かず、間に入る見出しを経由してしか繋がらないため。
            already_seen = any(same_event(tokens, p, generic) for p in seen_event_tokens)
            seen_event_tokens.append(tokens)
            if any(same_event(tokens, p, generic) for p in past_event_tokens):
                continue
            if already_seen:
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
