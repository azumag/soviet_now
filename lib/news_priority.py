#!/usr/bin/env python3
"""Political-priority selection for the radio current-news corner.

The caller already supplies an unread/public-interest pool. This module only
chooses one block: when both political and non-political blocks are available,
it chooses the political lane with a configurable share (67% by default), then
keeps the existing recency/source-diversity weighting inside that lane.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import os
import random
import sys
import unicodedata
from typing import Any

try:
    from news_filter import topic_family
except Exception:
    _NEWS_FILTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_filter.py")
    _NEWS_FILTER_SPEC = importlib.util.spec_from_file_location(
        "_news_filter_for_priority", _NEWS_FILTER_PATH
    )
    if _NEWS_FILTER_SPEC is None or _NEWS_FILTER_SPEC.loader is None:
        raise ImportError(f"cannot load {_NEWS_FILTER_PATH}")
    _NEWS_FILTER_MODULE = importlib.util.module_from_spec(_NEWS_FILTER_SPEC)
    _NEWS_FILTER_SPEC.loader.exec_module(_NEWS_FILTER_MODULE)
    topic_family = _NEWS_FILTER_MODULE.topic_family

DEFAULT_POLITICAL_SHARE = 0.67

_POLITICAL_SOURCE_KEYS = {
    "google_news_jp_politics",
    "nhk_politics",
}

# Strong signals only. Broad country names (China/US/Russia etc.) are
# deliberately omitted so an ordinary business story is not promoted merely
# because it mentions a country.
_POLITICAL_TERMS = (
    # Japanese: institutions / politicians / elections / parties
    "政府", "国会", "衆院", "参院", "衆議院", "参議院", "首相", "総理", "内閣",
    "大統領", "閣僚", "官房長官", "外相", "防衛相", "知事", "市長", "議会", "議員",
    "党首", "総裁", "選挙", "政党", "与党", "野党", "自民", "立憲", "立民", "公明",
    "維新", "国民民主", "れいわ", "共産党", "社民", "参政党", "連立", "不信任", "解散",
    "公約", "改憲", "憲法", "政治資金", "献金", "裏金",
    # Japanese: policy / government action
    "法案", "法改正", "法律", "政策", "規制", "行政", "自治体", "省庁", "官庁", "予算",
    "税制", "増税", "減税", "補助金", "給付", "社会保障", "年金", "最低賃金",
    # Japanese: diplomacy / security
    "外交", "首脳会談", "会談", "協議", "条約", "制裁", "停戦", "和平", "戦争", "軍事",
    "防衛", "安全保障", "安保", "領土", "関税", "国連", "nato", "g7", "g20",
    # English
    "government", "parliament", "congress", "prime minister", "president", "minister", "cabinet",
    "election", "lawmakers", "legislation", "political party", "ruling party", "opposition",
    "policy", "regulation", "budget", "tax reform", "tax cut", "tax hike", "subsidy",
    "diplomacy", "summit", "treaty", "sanction", "ceasefire", "peace talks", "war ", "military",
    "defense", "security policy", "tariff",
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def is_political_title(title: str, source_key: str = "") -> bool:
    """Return True for politics, policy, diplomacy or security news."""
    if (source_key or "").strip().lower() in _POLITICAL_SOURCE_KEYS:
        return True
    norm = _norm(title)
    return bool(norm) and any(term in norm for term in _POLITICAL_TERMS)


def political_share_from_env() -> float:
    """Read NEWS_POLITICAL_SHARE, falling back safely to the 67% policy."""
    raw = os.environ.get("NEWS_POLITICAL_SHARE", str(DEFAULT_POLITICAL_SHARE))
    try:
        share = float(raw)
    except (TypeError, ValueError):
        share = DEFAULT_POLITICAL_SHARE
    return min(1.0, max(0.0, share))


def _parse_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("■ "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _block_title(block: list[str]) -> str:
    return block[0][2:].strip() if block and block[0].startswith("■ ") else ""


def _block_topic_family(block: list[str], meta: dict[str, Any]) -> str:
    title = _block_title(block)
    item = meta.get(title, {}) if isinstance(meta, dict) else {}
    source_key = (item.get("source_key") or "").strip() if isinstance(item, dict) else ""
    return topic_family(title, source_key)


def topic_cooldown_from_env() -> int:
    """Return how many recently read topic families should be avoided."""
    raw = os.environ.get("NEWS_TOPIC_COOLDOWN", "2")
    try:
        return min(60, max(0, int(raw)))
    except (TypeError, ValueError):
        return 2


def _read_title_history(path: str, limit: int = 60) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    try:
        safe_limit = min(60, max(1, int(limit)))
    except (TypeError, ValueError):
        safe_limit = 60
    with open(path, encoding="utf-8", errors="ignore") as handle:
        titles = [line.strip() for line in handle if line.strip()]
    return titles[-safe_limit:]


def _topic_diverse_indexes(
    blocks: list[list[str]],
    candidate_indexes: list[int],
    recent_titles: list[str],
    *,
    meta: dict[str, Any],
    cooldown: int,
) -> list[int]:
    """Prefer a different coarse topic family when an alternative exists."""
    indexes = list(candidate_indexes)
    if not indexes or cooldown <= 0 or not recent_titles:
        return indexes

    recent_families = {
        topic_family(title)
        for title in recent_titles[-cooldown:]
    }
    blocked = {family for family in recent_families if family != "other"}
    if not blocked:
        return indexes

    allowed = [
        idx for idx in indexes
        if _block_topic_family(blocks[idx], meta) not in blocked
    ]
    # Fail open when the feed has temporarily narrowed to one topic family.
    return allowed or indexes


def _name_to_key(name: str) -> str:
    """Match the legacy radio_news.sh source-family mapping exactly."""
    if name == "ウィキニュース" or name.startswith("Wikinews"):
        return "wikinews"
    if name.startswith("Google News"):
        return "google_news"
    return {"Global Voices": "globalvoices"}.get(name, "")


def _published_ts(meta: dict[str, Any], title: str) -> int:
    item = meta.get(title, {}) if isinstance(meta, dict) else {}
    try:
        return int(item.get("published_ts", 0) or 0)
    except Exception:
        return 0


def _read_source_counts(history_file: str) -> dict[str, int]:
    """Count only the last 12 source families, as the legacy picker did."""
    if not history_file or not os.path.exists(history_file):
        return {}
    with open(history_file, encoding="utf-8", errors="ignore") as handle:
        history = [line.strip() for line in handle if line.strip()]
    return dict(Counter(history[-12:]))


def _load_meta(path: str = "tmp/news_meta.json") -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_weights(
    blocks: list[list[str]],
    *,
    meta: dict[str, Any],
    source_counts: dict[str, int],
) -> list[float]:
    """Return the exact pre-priority picker weights for the supplied blocks."""
    published_values = [
        _published_ts(meta, _block_title(block))
        for block in blocks
    ]
    newest_ts = max(published_values) if published_values else 0

    weights: list[float] = []
    for block in blocks:
        title = _block_title(block)
        item = meta.get(title, {}) if isinstance(meta, dict) else {}
        source_name = (item.get("source") or "").strip()
        source_key = _name_to_key(source_name)
        freq = source_counts.get(source_key, 0) if source_key else 0
        published_ts = _published_ts(meta, title)
        if newest_ts > 0 and published_ts > 0:
            age_hours = max(0.0, (newest_ts - published_ts) / 3600.0)
            recency_weight = 1.0 / (1.0 + age_hours / 12.0)
        elif published_ts > 0:
            recency_weight = 1.0
        else:
            recency_weight = 0.25
        source_weight = 1.0 / (1 + freq)
        weights.append((recency_weight * 6.0) + source_weight)
    return weights


def choose_news_block(
    blocks_text: str,
    *,
    meta: dict[str, Any] | None = None,
    source_counts: dict[str, int] | None = None,
    political_share: float = DEFAULT_POLITICAL_SHARE,
    rng: Any = random,
    recent_titles: list[str] | None = None,
) -> str:
    """Choose one news block while preserving legacy within-lane weighting."""
    blocks = _parse_blocks(blocks_text)
    if not blocks:
        return ""

    meta = meta if isinstance(meta, dict) else {}
    source_counts = source_counts if isinstance(source_counts, dict) else {}
    share = min(1.0, max(0.0, float(political_share)))

    political_flags: list[bool] = []
    for block in blocks:
        title = _block_title(block)
        item = meta.get(title, {}) if isinstance(meta, dict) else {}
        source_key = (item.get("source_key") or "").strip()
        political_flags.append(is_political_title(title, source_key))

    # Compute weights across the complete unread pool exactly as the legacy
    # picker did. Topic rotation and lane selection only restrict which
    # already-weighted entries may win.
    weights = _legacy_weights(blocks, meta=meta, source_counts=source_counts)

    topic_indexes = _topic_diverse_indexes(
        blocks,
        list(range(len(blocks))),
        recent_titles or [],
        meta=meta,
        cooldown=topic_cooldown_from_env(),
    )
    political_indexes = [idx for idx in topic_indexes if political_flags[idx]]
    other_indexes = [idx for idx in topic_indexes if not political_flags[idx]]
    if political_indexes and other_indexes:
        candidate_indexes = political_indexes if rng.random() < share else other_indexes
    else:
        candidate_indexes = political_indexes or other_indexes

    candidate_weights = [weights[idx] for idx in candidate_indexes]
    chosen_index = rng.choices(candidate_indexes, weights=candidate_weights, k=1)[0]
    return "\n".join(blocks[chosen_index])


def main(argv: list[str]) -> int:
    if len(argv) not in {3, 4}:
        print(
            "usage: news_priority.py <past-source-history> <blocks-text> [past-title-history]",
            file=sys.stderr,
        )
        return 2
    history_file, blocks_text = argv[1], argv[2]
    recent_titles = _read_title_history(argv[3]) if len(argv) == 4 else []
    chosen = choose_news_block(
        blocks_text,
        meta=_load_meta(),
        source_counts=_read_source_counts(history_file),
        political_share=political_share_from_env(),
        recent_titles=recent_titles,
    )
    if chosen:
        print(chosen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
