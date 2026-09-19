#!/usr/bin/env python3
"""Public-facing Twitch title selection and composition helpers.

The operational brief is intentionally *not* a title source.  Public titles must
come from an explicit viewer-facing candidate, an already-safe current title,
or a neutral fallback.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_FALLBACK = "AIたちがゲーム・ニュース・会話に挑戦する実験配信"
TITLE_LIMIT = 140

_DAY_PREFIX = re.compile(r"(?i)^\s*\[\s*day\s*\d+\s*\]\s*")
_LEGACY_PREFIX = re.compile(r"(?i)^\s*\[[^\]]+\]\s*day\s*\d+\b\s*")
_FREE_DAY = re.compile(r"(?i)\bday\s*\d+\b")
_INTERNAL_PATTERNS = (
    re.compile(r"(?i)\b(?:PR|MR)\s*#?\s*\d+\b"),
    re.compile(r"(?i)\b(?:issue|pull\s*request)\s*#?\s*\d+\b"),
    re.compile(r"(?i)\b[\w.-]+#\d+\b"),
    re.compile(r"(?i)\b(?:main|master|HEAD|SHA|CI|VM)\b"),
    re.compile(r"(?i)(?:マージ|merge(?:d)?|デプロイ|deploy(?:ed)?|コミット|commit(?:ted)?|本番反映|参照更新)"),
    re.compile(r"(?i)\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"),
    re.compile(r"(?i)(?:^|\s)(?:[\w.-]+/)+[\w.-]+\.(?:py|sh|toml|ya?ml|json|md)\b"),
)


def collapse(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split())


def strip_title_prefix(text: str) -> str:
    body = collapse(text)
    match = _DAY_PREFIX.match(body)
    if match:
        return body[match.end() :].strip()

    match = _LEGACY_PREFIX.match(body)
    if match:
        return body[match.end() :].strip()

    match = _FREE_DAY.search(body)
    if match:
        return collapse(body[: match.start()] + " " + body[match.end() :])
    return body


def looks_internal(text: str) -> bool:
    value = collapse(text)
    if not value:
        return True
    return any(pattern.search(value) for pattern in _INTERNAL_PATTERNS)


def public_text(text: str) -> str:
    value = collapse(text)
    return "" if looks_internal(value) else value


def read_candidate(path: str | None) -> str:
    if not path:
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if re.match(r"(?i)^(?:status|viewer_title_status)\s*:", line):
            continue
        return public_text(line)
    return ""


def safe_fallback(value: str | None) -> str:
    candidate = public_text(value or "")
    return candidate or DEFAULT_FALLBACK


def choose_body(*, current: str, candidate_file: str | None, fallback: str | None) -> str:
    candidate = read_candidate(candidate_file)
    if candidate:
        return candidate

    current_body = public_text(strip_title_prefix(current))
    if current_body:
        return current_body

    return safe_fallback(fallback)


def shorten(text: str, limit: int) -> str:
    value = collapse(text)
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return value[: limit - 1].rstrip("、。 ,.") + "…"


def compose_title(
    *,
    day: str,
    activity: str | None,
    strategy: str | None,
    candidate_file: str | None,
    fallback: str | None,
) -> str:
    prefix = f"[day{collapse(day)}]"

    if activity is None:
        public_activity = read_candidate(candidate_file)
    else:
        public_activity = public_text(activity)
    if not public_activity:
        public_activity = safe_fallback(fallback)

    public_strategy = public_text(strategy or "")
    if public_strategy:
        public_strategy = shorten(public_strategy, 40)

    # Activity is the core public description.  Strategy is optional and loses
    # space first so the title never degrades into an opaque implementation log.
    reserved = len(prefix) + 1
    strategy_cost = 1 + len(public_strategy) if public_strategy else 0
    activity_limit = max(0, TITLE_LIMIT - reserved - strategy_cost)
    public_activity = shorten(public_activity, activity_limit)

    parts = [prefix]
    if public_activity:
        parts.append(public_activity)

    if public_strategy:
        remaining = TITLE_LIMIT - len(" ".join(parts)) - 1
        if remaining >= 4:
            parts.append(shorten(public_strategy, remaining))

    return " ".join(parts)[:TITLE_LIMIT]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    choose = sub.add_parser("choose")
    choose.add_argument("--current", required=True)
    choose.add_argument("--candidate-file")
    choose.add_argument("--fallback")

    compose = sub.add_parser("compose")
    compose.add_argument("--day", required=True)
    compose.add_argument("--activity")
    compose.add_argument("--strategy", default="")
    compose.add_argument("--candidate-file")
    compose.add_argument("--fallback")

    args = parser.parse_args()

    if args.command == "choose":
        print(
            choose_body(
                current=args.current,
                candidate_file=args.candidate_file,
                fallback=args.fallback,
            )
        )
        return 0

    print(
        compose_title(
            day=args.day,
            activity=args.activity,
            strategy=args.strategy,
            candidate_file=args.candidate_file,
            fallback=args.fallback,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
