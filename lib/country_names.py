"""Authoritative Soviet-game stage-to-country names."""

from __future__ import annotations

import math
import re


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


_REASON_COUNTRY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[Tt](1[0-6]|[1-9])(?=_|$)"
)


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


def last_drop_turn_country_label(record: object) -> str:
    """Format the turn and dropped country without exposing an internal ID.

    Historical records may predate ``next_type``; those keep the turn label
    alone.  If the field exists but is invalid, surface ``不明な国`` so corrupt
    data is distinguishable from an older record.
    """
    if not isinstance(record, dict):
        return "?手目"
    turn = str(record.get("turn", "?"))
    danger = bool(
        record.get("deadline_crossed") or record.get("decision_crosses_deadline")
    )
    turn_label = f"{turn}手目{'!' if danger else ''}"
    if "next_type" not in record:
        return turn_label
    return f"{turn_label} {country_name(record.get('next_type'))}"


def country_named_reason(reason: object, default: str = "?") -> str:
    """Replace stage tokens embedded in strategy reason identifiers.

    Strategy history intentionally keeps stable machine-facing identifiers such
    as ``FIRST_RUSSIA_T11_LANE_COVER_AVOID``.  User-facing dashboards and
    overlays must not expose that internal stage number, so only standalone
    identifier tokens are replaced here.  Unknown tokens remain unchanged.
    """
    text = str(reason or "").strip()
    if not text:
        return default

    def replace(match: re.Match[str]) -> str:
        return COUNTRY_NAMES.get(int(match.group(1)), match.group(0))

    return _REASON_COUNTRY_TOKEN_RE.sub(replace, text)
