#!/usr/bin/env python3
"""Sports topic filter for news/jiji.

Used by fetch_news.py, fetch_google_headlines.py, news_filter.py and shell-inline filters
to exclude baseball / sports topics per user request.

English and Japanese keywords are covered.  The check is intentionally conservative:
only titles that clearly contain a sports-specific term are excluded.
Generic competition words like "優勝/試合" are NOT included to avoid false positives
on politics/economics news.

Toggle:
    SPORTS_FILTER_DISABLED=1  -> is_sports_title() always returns False

Extra keywords:
    SPORTS_FILTER_EXTRA_KEYWORDS  comma-separated list appended to the default set
"""
from __future__ import annotations

import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Japanese keywords (substring match after NFKC+lower)
# ---------------------------------------------------------------------------
_JA_KEYWORDS_RAW = [
    # generic
    "野球",
    "プロ野球",
    "甲子園",
    "高校野球",
    "セ・リーグ",
    "パ・リーグ",
    "セリーグ",
    "パリーグ",
    "侍ジャパン",
    "ドジャース",
    "大谷翔平",
    "大谷",  # Shohei Ohtani - dominates baseball news
    "ダルビッシュ",
    "山本由伸",
    "佐々木朗希",
    "イチロー",
    "本塁打",
    "ホームラン",
    "大リーグ",
    "メジャーリーグ",
    "スポーツ",
    "アスリート",
    "競技",
    # baseball team nicknames (katakana - specific to sports)
    "タイガース",
    "ジャイアンツ",
    "カープ",
    "ドラゴンズ",
    "スワローズ",
    "ベイスターズ",
    "バファローズ",
    "ホークス",
    "マリーンズ",
    "ライオンズ",
    "イーグルス",
    "ファイターズ",
    "センバツ",
    "オオタニ",
    # soccer / football
    "サッカー",
    "Ｊリーグ",
    "Jリーグ",
    "ワールドカップ",
    "Ｗ杯",
    "W杯",
    # basketball
    "バスケ",
    "バスケットボール",
    "Ｂリーグ",
    "Bリーグ",
    # tennis / golf
    "テニス",
    "ゴルフ",
    # sumo / martial
    "相撲",
    "大相撲",
    "横綱",
    "力士",
    "柔道",
    "剣道",
    "空手",
    "ボクシング",
    "格闘技",
    "総合格闘技",
    "ＵＦＣ",
    "Ｋ－１",
    "K-1",
    # horse racing
    "競馬",
    "ダービー",
    "有馬記念",
    "ＪＲＡ",
    "JRA",
    # athletics
    "マラソン",
    "駅伝",
    "陸上競技",
    # ball sports
    "バレーボール",
    "バレー",
    "ラグビー",
    "ハンドボール",
    "卓球",
    "バドミントン",
    # winter / other
    "フィギュアスケート",
    "フィギュア",
    "水泳",
    "競泳",
    "体操",
    "新体操",
    "スキー",
    "スノーボード",
    "スノボ",
    "サーフィン",
    "カーリング",
    "スケート",
    "アイスホッケー",
    "ホッケー",
    "クリケット",
    "ソフトボール",
    "アメリカンフットボール",
    "アメフト",
    # motor / olympic
    "Ｆ１",
    "F1",
    "モータースポーツ",
    "グランプリ",
    "オリンピック",
    "パラリンピック",
    "五輪",
    "ｅスポーツ",
    "eスポーツ",
]

# Normalize keywords once at import time
def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().lower()

_JA_KEYWORDS = [_norm(k) for k in _JA_KEYWORDS_RAW if _norm(k)]

# Allow extra keywords via env
_extra = os.environ.get("SPORTS_FILTER_EXTRA_KEYWORDS", "")
if _extra.strip():
    for kw in _extra.split(","):
        kw = _norm(kw)
        if kw and kw not in _JA_KEYWORDS:
            _JA_KEYWORDS.append(kw)

# ---------------------------------------------------------------------------
# English keywords (word-boundary regex, case-insensitive)
# ---------------------------------------------------------------------------
_EN_KEYWORDS = [
    r"baseball",
    r"softball",
    r"mlb",
    r"npb",
    r"wbc",  # World Baseball Classic
    r"ohtani",
    r"darvish",
    r"dodgers",
    r"yankees",
    r"home\s*run",
    r"homerun",
    r"pitcher",
    r"innings?",
    r"soccer",
    r"football",  # includes American football
    r"basketball",
    r"\bnba\b",
    r"\bnfl\b",
    r"tennis",
    r"golf",
    r"sumo",
    r"rugby",
    r"volleyball",
    r"marathon",
    r"athletics",
    r"boxing",
    r"judo",
    r"kendo",
    r"karate",
    r"wrestling",
    r"figure\s*skating",
    r"skiing",
    r"snowboard",
    r"surfing",
    r"gymnastics",
    r"badminton",
    r"table\s*tennis",
    r"formula\s*1",
    r"\bf1\b",
    r"motogp",
    r"olympic",
    r"paralympic",
    r"sports?",
    r"athlete",
    r"premier\s*league",
    r"champions\s*league",
    r"world\s*cup",
    r"grand\s*slam",
    r"uefa",
    r"fifa",
]

# Frequent non-English sports phrases seen in Global Voices feeds.
_OTHER_LANGUAGE_KEYWORDS = [
    "كأس العالم",       # Arabic: World Cup
    "copa do mundo",    # Portuguese
    "copa mundial",     # Spanish
    "coupe du monde",   # French
    "чемпионат мира",   # Russian
]

# Build regex: each keyword already may contain \b, but wrap overall
_EN_PATTERN = re.compile(
    r"(?:%s)" % "|".join(_EN_KEYWORDS),
    re.IGNORECASE,
)

def is_sports_title(title: str) -> bool:
    """Return True if title looks like a baseball/sports topic."""
    if os.environ.get("SPORTS_FILTER_DISABLED", "0") == "1":
        return False
    if not title or not title.strip():
        return False
    norm = _norm(title)

    if any(keyword in norm for keyword in _OTHER_LANGUAGE_KEYWORDS):
        return True

    # Japanese substring check
    for kw in _JA_KEYWORDS:
        if kw and kw in norm:
            return True

    # English word-boundary check on original (case-insensitive)
    # Use original title for \b to work on ascii words; also try norm
    if _EN_PATTERN.search(title):
        return True
    if _EN_PATTERN.search(norm):
        return True
    return False


# Convenience for filter stats
FILTER_REASON_SPORTS = "sports"
