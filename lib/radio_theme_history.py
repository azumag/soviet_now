"""Offline topic matching using the public catalog and existing selection history.

History text is never returned to a prompt. Only exact catalog matches can supply
the public titles used by ``recent``; generated radio summaries are not inputs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


def normalize(text: str) -> str:
    text = re.sub(r"^\[soviet\]\s*", "", text or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"を深掘りして|を深掘り|深掘りして|深掘り", " ", text)
    text = re.sub(r"の話(?:。)?", " ", text)
    text = re.sub(r"[()（）「」『』【】［］\[\]!?！？:：]", " ", text)
    text = re.sub(r"[、,／/・;；]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def keywords(text: str) -> set[str]:
    stop = {
        "ソ連", "ロシア", "日本", "世界", "歴史", "文化", "政治", "経済", "思想", "哲学",
        "社会", "事件", "人物", "制度", "理論", "技術", "国家", "革命", "問題", "テーマ",
        "放送", "深掘り", "構造", "背景", "比較", "現実", "真実", "心理", "起源", "実態",
    }
    return {
        part.strip()
        for chunk in normalize(text).split()
        for part in re.split(r"(?:の|と|や|を|に|で|へ|から|まで|について|による|によると)", chunk)
        if len(part.strip()) >= 3 and part.strip() not in stop
    }


def positive_limit(value: str | int, default: int = 400) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def read_tail(path: str | Path, limit: int) -> list[str]:
    try:
        rows = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [row.strip() for row in rows if row.strip()][-limit:]


def catalog_candidates(path: str | Path, category: str = "") -> list[str]:
    try:
        rows = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    seen = set()
    candidates = []
    for row in rows:
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        row_category = "soviet" if row.startswith("[soviet] ") else ""
        if category and category != row_category:
            continue
        key = normalize(row)
        if key and key not in seen:
            seen.add(key)
            candidates.append(row)
    return candidates


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    families: frozenset[str]


def read_catalog(path: str | Path) -> dict[str, Topic]:
    """A family comment lists exact public titles; all other comments are ignored."""
    try:
        rows = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    families: dict[str, set[str]] = {}
    for row in rows:
        match = re.fullmatch(r"# family: ([a-z0-9_-]{1,64})\s*\|\s*(.+)", row)
        if match:
            family, titles = match.groups()
            for title in titles.split("|"):
                families.setdefault(normalize(title.strip()), set()).add(family)
    catalog = {}
    for row in rows:
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        body = re.sub(r"^\[soviet\]\s*", "", row)
        title = body.split("の話。", 1)[0]
        key = normalize(body)
        catalog[key] = Topic(key, title, frozenset(families.get(normalize(title), set())))
    return catalog


@dataclass(frozen=True)
class MatchData:
    key: str
    words: frozenset[str]
    families: frozenset[str]


def prepare(text: str, catalog: dict[str, Topic]) -> MatchData:
    key = normalize(text)
    topic = catalog.get(key)
    return MatchData(key, frozenset(keywords(text)), topic.families if topic else frozenset())


def match_mode(candidate: str, past: str, catalog: dict[str, Topic]) -> str:
    return compare(prepare(candidate, catalog), prepare(past, catalog))


def compare(candidate: MatchData, past: MatchData) -> str:
    if not candidate.key or not past.key:
        return ""
    if candidate.key == past.key:
        return "exact"
    shared_families = candidate.families & past.families
    if shared_families:
        return "family:" + sorted(shared_families)[0]
    shared = candidate.words & past.words
    if any(len(token) >= 5 for token in shared) or len(shared) >= 2:
        return "overlap:" + ",".join(sorted(shared, key=lambda token: (-len(token), token))[:3])
    return ""


def recent_match(candidate: str, histories: list[list[str]], catalog: dict[str, Topic]) -> str:
    return prepared_recent_match(prepare(candidate, catalog), [[prepare(past, catalog) for past in rows] for rows in histories])


def prepared_recent_match(candidate: MatchData, histories: list[list[MatchData]]) -> str:
    # Preserve exact-key priority even if a family peer was selected more recently.
    if candidate.key and any(candidate.key == past.key for rows in histories for past in rows):
        return "exact"
    for rows in histories:
        for past in reversed(rows):
            mode = compare(candidate, past)
            if mode:
                return mode
    return ""


def oldest_candidates(candidates: list[str], histories: list[list[str]], catalog: dict[str, Topic]) -> list[str]:
    """Reuse only the least recently matched candidates when the pool is exhausted."""
    prepared_histories = [[prepare(past, catalog) for past in rows] for rows in histories]
    ranks = []
    for candidate in candidates:
        prepared_candidate = prepare(candidate, catalog)
        ages = [
            age for rows in prepared_histories for age, past in enumerate(reversed(rows))
            if compare(prepared_candidate, past)
        ]
        ranks.append(min(ages) if ages else float("inf"))
    if not ranks:
        return []
    oldest = max(ranks)
    return [candidate for candidate, rank in zip(candidates, ranks) if rank == oldest]


def recent_public_topics(histories: list[list[str]], catalog: dict[str, Topic], limit: int, exclude: str = "") -> list[Topic]:
    # Use both legacy keys-only and bodies histories, newest first. Never echo a row.
    current = catalog.get(normalize(exclude))
    seen: set[str] = set()
    topics = []
    by_age = sorted((age, i, row) for i, rows in enumerate(histories) for age, row in enumerate(reversed(rows)))
    for _age, _i, row in by_age:
        topic = catalog.get(normalize(row))
        if not topic or topic.key in seen:
            continue
        seen.add(topic.key)
        if current and (topic.key == current.key or topic.families & current.families):
            continue
        topics.append(topic)
        if len(topics) >= limit:
            break
    return topics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("key", "catalog", "match", "available", "oldest", "recent"))
    parser.add_argument("--body", default="")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--bodies", default="")
    parser.add_argument("--keys", default="")
    parser.add_argument("--keep", default="400")
    parser.add_argument("--limit", default="12")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--category", default="")
    args = parser.parse_args()
    if args.command == "key":
        print(normalize(args.body))
        return
    if args.command == "catalog":
        for candidate in catalog_candidates(args.catalog, args.category):
            print(candidate)
        return
    catalog = read_catalog(args.catalog)
    histories = [read_tail(path, positive_limit(args.keep)) for path in (args.bodies, args.keys)]
    if args.command == "match":
        mode = recent_match(args.body, histories, catalog)
        if mode:
            print(mode)
    elif args.command in ("oldest", "available"):
        candidates = [line.rstrip("\n") for line in sys.stdin if line.strip()]
        if args.command == "oldest":
            selected = oldest_candidates(candidates, histories, catalog)
        else:
            prepared_histories = [[prepare(past, catalog) for past in rows] for rows in histories]
            selected = [candidate for candidate in candidates if not prepared_recent_match(prepare(candidate, catalog), prepared_histories)]
        for candidate in selected:
            print(candidate)
    else:
        topics = recent_public_topics(histories, catalog, positive_limit(args.limit, 12), args.exclude)
        if topics:
            print("直近で選んだ脱線題材（公開カタログの題材名のみ）:")
            for topic in topics:
                print("- " + topic.title)
            print("- 今回指定された題材を優先し、それ以外の直近題材や同系の話への脱線を繰り返さないこと")


if __name__ == "__main__":
    main()
