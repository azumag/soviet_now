"""Authoritative Soviet-game stage-to-country names."""

from __future__ import annotations

import math


COUNTRY_NAMES = {
    1: "アルメニア",
    2: "モルドバ",
    3: "エストニア",
    4: "ラトビア",
    5: "リトアニア",
    6: "ジョージア",
    7: "アゼルバイジャン",
    8: "タジキスタン",
    9: "キルギス",
    10: "ベラルーシ",
    11: "ウズベキスタン",
    12: "トルクメニスタン",
    13: "ウクライナ",
    14: "カザフスタン",
    15: "ロシア",
    16: "ソ連",
}


def country_name(piece_type: object, default: str = "不明な国") -> str:
    """Return a user-facing country name without exposing an internal stage ID."""
    if isinstance(piece_type, bool):
        return default
    if isinstance(piece_type, float):
        if not math.isfinite(piece_type) or not piece_type.is_integer():
            return default
    try:
        normalized = int(piece_type)
    except (TypeError, ValueError):
        return default
    return COUNTRY_NAMES.get(normalized, default)
