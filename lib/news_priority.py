#!/usr/bin/env python3
"""Political-priority selection for the radio current-news corner.

The caller already supplies an unread/public-interest pool. This module only
chooses one block: when both political and non-political blocks are available,
it chooses the political lane with a configurable share (67% by default), then
keeps the existing recency/source-diversity weighting inside that lane.
"""
from __future__ import annotations

import json
import os
import random
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any

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


def _name_to_key(name: str) -> str:
    normalized = (name or "").lower()
    if "nhk" in normalized:
        return "nhk"
    if "global voices" in normalized:
        return "globalvoices"
    if "google news" in normalized:
        return "google_news"
    if "wikinews" in normalized:
        return "wikinews"
    return ""


def _published_at(meta: dict[str, Any], title: str) -> datetime | None:
    item = meta.get(title, {}) if isinstance(meta, dict) else {}
    value = (item.get("published_at") or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _read_source_counts(history_file: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not history_file or not os.path.exists(history_file):
        return counts
    with open(history_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            key = line.strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _load_meta(path: str = "tmp/news_meta.json") -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def choose_news_block(
    blocks_text: str,
    *,
    meta: dict[str, Any] | None = None,
    source_counts: dict[str, int] | None = None,
    political_share: float = DEFAULT_POLITICAL_SHARE,
    rng: Any = random,
) -> str:
    """Choose one news block while preserving legacy within-lane weighting."""
    blocks = _parse_blocks(blocks_text)
    if not blocks:
        return ""

    meta = meta if isinstance(meta, dict) else {}
    source_counts = source_counts if isinstance(source_counts, dict) else {}
    share = min(1.0, max(0.0, float(political_share)))

    source_freqs: list[int] = []
    published: list[datetime | None] = []
    political_flags: list[bool] = []
    for block in blocks:
        title = block[0][2:].strip()
        item = meta.get(title, {}) if isinstance(meta, dict) else {}
        source_name = (item.get("source") or "").strip()
        source_key = (item.get("source_key") or "").strip()
        family_key = _name_to_key(source_name)
        source_freqs.append(source_counts.get(family_key, 0) if family_key else 0)
        published.append(_published_at(meta, title))
        political_flags.append(is_political_title(title, source_key))

    valid_times = [value for value in published if value is not None]
    newest = max(valid_times) if valid_times else None
    weights: list[float] = []
    for idx, _block in enumerate(blocks):
        recency_weight = 1.0
        dt = published[idx]
        if newest is not None and dt is not None:
            age_hours = max(0.0, (newest - dt).total_seconds() / 3600.0)
            recency_weight = max(0.2, 1.0 - min(age_hours, 48.0) / 60.0)
        source_weight = 1.0 / (1 + source_freqs[idx])
        weights.append((recency_weight * 6.0) + source_weight)

    political_indexes = [idx for idx, value in enumerate(political_flags) if value]
    other_indexes = [idx for idx, value in enumerate(political_flags) if not value]
    if political_indexes and other_indexes:
        candidate_indexes = political_indexes if rng.random() < share else other_indexes
    else:
        candidate_indexes = political_indexes or other_indexes

    candidate_weights = [weights[idx] for idx in candidate_indexes]
    chosen_index = rng.choices(candidate_indexes, weights=candidate_weights, k=1)[0]
    return "\n".join(blocks[chosen_index])


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: news_priority.py <past-source-history> <blocks-text>", file=sys.stderr)
        return 2
    history_file, blocks_text = argv[1], argv[2]
    chosen = choose_news_block(
        blocks_text,
        meta=_load_meta(),
        source_counts=_read_source_counts(history_file),
        political_share=political_share_from_env(),
    )
    if chosen:
        print(chosen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
