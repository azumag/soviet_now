#!/usr/bin/env python3
"""Public-facing Twitch title selection and composition helpers.

The operational brief is intentionally *not* a title source.  Public titles must
come from an explicit viewer-facing candidate *with valid metadata*, an
already-safe current title, or a neutral fallback.

Candidate contract (docich#789):

- ``viewer_title`` (or ``視聴者向けタイトル``): the published text itself.
- ``rationale`` (or ``根拠``): why the wording is right.  Internal only; never
  published.
- ``status`` (or ``確認段階``): one of ``implemented`` / ``merged`` /
  ``deployed`` / ``verified``.  Only ``deployed`` / ``verified`` candidates are
  adopted for publication (the stage gate is a design decision under owner
  review; the safe default keeps unconfirmed stages private).
- ``generated_at`` (or ``生成日時``): ISO-8601 timestamp (UTC).  Candidates
  older than the TTL are never adopted.
- ``valid_scope`` (or ``有効範囲``): optional program scope (e.g. a game
  name).  A scoped candidate is only adopted while the live category matches.

Missing, unparsable or out-of-contract metadata is fail-closed: the selector
falls back to a safe current body or the neutral fallback instead of guessing.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_FALLBACK = "AIたちがゲーム・ニュース・会話に挑戦する実験配信"
TITLE_LIMIT = 140

DEFAULT_TTL_DAYS = 7
PUBLISHABLE_STATUSES = ("deployed", "verified")
KNOWN_STATUSES = ("implemented", "merged", "deployed", "verified")

_DAY_PREFIX = re.compile(r"(?i)^\s*\[\s*day\s*\d+\s*\]\s*")
_LEGACY_PREFIX = re.compile(r"(?i)^\s*\[[^\]]+\]\s*day\s*\d+\b\s*")
_FREE_DAY = re.compile(r"(?i)\bday\s*\d+\b")
# Python の \b は Unicode \w を使うため、日本語の助詞・漢字かなが ASCII 識別子に
# 直接隣接すると境界が成立しない（例: 「現行mainへ」「#899を」）。そこで識別子系の
# パターンは、ASCII の英数字とアンダースコアだけを語構成文字とする lookaround で
# 境界を定義する。これで「PR #899を統合・現行mainへVM反映」のような実運用形式も
# 漏れなく検出しつつ、"maintain" のような語中の一致は拒否する。
_ASCII_L = r"(?<![0-9A-Za-z_])"
_ASCII_R = r"(?![0-9A-Za-z_])"
_INTERNAL_PATTERNS = (
    re.compile(r"(?i)" + _ASCII_L + r"(?:PR|MR)[\s#\-]*\d+" + _ASCII_R),
    re.compile(r"(?i)" + _ASCII_L + r"(?:issue|pull\s*request)[\s#\-]*\d+" + _ASCII_R),
    re.compile(r"(?i)" + _ASCII_L + r"[\w.-]+#\d+" + _ASCII_R),
    re.compile(r"(?i)" + _ASCII_L + r"(?:main|master|HEAD|SHA|CI|VM)" + _ASCII_R),
    re.compile(r"(?i)" + _ASCII_L + r"root\s+v\d+" + _ASCII_R),
    re.compile(r"(?i)(?:マージ|merge(?:d)?|デプロイ|deploy(?:ed)?|コミット|commit(?:ted)?|本番反映|参照更新)"),
    re.compile(
        r"(?i)"
        + _ASCII_L
        + r"(?=[0-9a-f]{7,40}"
        + _ASCII_R
        + r")(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}"
    ),
    re.compile(r"(?i)" + _ASCII_L + r"(?:[\w.-]+/)+[\w.-]+\.(?:py|sh|toml|ya?ml|json|md)" + _ASCII_R),
)

_KEY_LINE = re.compile(r"^(?:[-*]\s*)?([A-Za-z_][\w]*|視聴者向けタイトル|根拠|確認段階|生成日時|有効範囲)\s*[:：]\s*(.+)$")
_KEY_ALIASES = {
    "viewer_title": "viewer_title",
    "視聴者向けタイトル": "viewer_title",
    "rationale": "rationale",
    "根拠": "rationale",
    "status": "status",
    "確認段階": "status",
    "viewer_title_status": "status",
    "generated_at": "generated_at",
    "生成日時": "generated_at",
    "valid_scope": "valid_scope",
    "有効範囲": "valid_scope",
}


def ttl_days() -> int:
    raw = os.environ.get("STREAM_VIEWER_TITLE_TTL_DAYS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TTL_DAYS
    return value if value >= 0 else DEFAULT_TTL_DAYS


def collapse(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split())


def strip_title_prefix(text: str) -> str:
    body = collapse(text)
    match = _DAY_PREFIX.match(body)
    if match:
        return body[match.end() :].strip()

    match = _LEGACY_PREFIX.match(body)
    if match:
        return body[match.end():].strip()

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


@dataclass(frozen=True)
class ViewerCandidate:
    viewer_title: str
    rationale: str
    status: str
    generated_at: datetime
    valid_scope: str | None = None

    @property
    def is_publishable_stage(self) -> bool:
        """Only stages whose effects are confirmed may be published.

        ``implemented`` / ``merged`` stay private so a merge-only change is
        never announced as an on-stream improvement (docich#789).
        """
        return self.status in PUBLISHABLE_STATUSES

    def is_fresh(self, *, now: datetime | None = None) -> bool:
        """Fresh means parsed within the TTL and never dated in the future."""
        now = now or datetime.now(timezone.utc)
        if self.generated_at > now:
            return False
        return (now - self.generated_at) <= timedelta(days=ttl_days())

    def scope_matches(self, current_scope: str | None) -> bool:
        """A scoped candidate only applies while the live category matches."""
        if not self.valid_scope:
            return True
        if not current_scope:
            return False
        return self.valid_scope.casefold() in current_scope.casefold()


def parse_viewer_candidate(path: str | None) -> ViewerCandidate | None:
    """Parse the structured candidate file; return None on any contract miss.

    A bare title line (the pre-metadata format) carries no stage/freshness
    contract and is intentionally not adopted.
    """
    if not path:
        return None
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    data: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_LINE.match(line)
        if not match:
            continue
        key = _KEY_ALIASES.get(match.group(1).casefold() if match.group(1).isascii() else match.group(1))
        if key and key not in data:
            data[key] = match.group(2).strip()

    viewer_title = data.get("viewer_title", "").strip()
    if not viewer_title:
        return None

    status = data.get("status", "").strip().casefold()
    if status not in KNOWN_STATUSES:
        return None

    generated_at = _parse_timestamp(data.get("generated_at", ""))
    if generated_at is None:
        return None

    return ViewerCandidate(
        viewer_title=viewer_title,
        rationale=data.get("rationale", ""),
        status=status,
        generated_at=generated_at,
        valid_scope=(data.get("valid_scope", "").strip() or None),
    )


def _parse_timestamp(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00").replace("z", "+00:00")
    if "T" not in normalized and " " not in normalized:
        normalized += "T00:00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def adoptable_candidate(
    candidate_file: str | None, current_scope: str | None
) -> ViewerCandidate | None:
    """Return the candidate only when the whole contract holds."""
    candidate = parse_viewer_candidate(candidate_file)
    if candidate is None:
        return None
    if not candidate.is_publishable_stage:
        return None
    if not candidate.rationale.strip():
        # docich#789 §1: the stated rationale ("何の変更かを説明する根拠") is part
        # of the minimum candidate contract.  A rationale-free candidate cannot
        # be audited, so it is never adopted.
        return None
    if not candidate.is_fresh():
        return None
    if not candidate.scope_matches(current_scope):
        return None
    if not public_text(candidate.viewer_title):
        return None
    return candidate


def read_candidate(path: str | None, current_scope: str | None = None) -> str:
    candidate = adoptable_candidate(path, current_scope)
    if candidate is None:
        return ""
    return public_text(candidate.viewer_title)


def safe_fallback(value: str | None) -> str:
    candidate = public_text(value or "")
    return candidate or DEFAULT_FALLBACK


def choose_body(
    *,
    current: str,
    candidate_file: str | None,
    fallback: str | None,
    current_scope: str | None = None,
) -> str:
    candidate = parse_viewer_candidate(candidate_file)
    if candidate is not None:
        if adoptable_candidate(candidate_file, current_scope) is not None:
            return public_text(candidate.viewer_title)
        # The file carries a well-formed candidate that fails the contract
        # (unconfirmed stage, expired, or out-of-scope).  If the current body is
        # literally that same text, preserving it would re-adopt known-
        # disqualified content through the back door; move to the neutral
        # fallback.  A *different* safe current body is still preserved
        # (docich#789 review: distinguish the two behaviors).
        if public_text(candidate.viewer_title) == public_text(strip_title_prefix(current)):
            return safe_fallback(fallback)

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
    current_scope: str | None = None,
) -> str:
    prefix = f"[day{collapse(day)}]"

    if activity is None:
        public_activity = read_candidate(candidate_file, current_scope)
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
    choose.add_argument("--current-scope")

    compose = sub.add_parser("compose")
    compose.add_argument("--day", required=True)
    compose.add_argument("--activity")
    compose.add_argument("--strategy", default="")
    compose.add_argument("--candidate-file")
    compose.add_argument("--fallback")
    compose.add_argument("--current-scope")

    args = parser.parse_args()

    if args.command == "choose":
        print(
            choose_body(
                current=args.current,
                candidate_file=args.candidate_file,
                fallback=args.fallback,
                current_scope=args.current_scope or None,
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
            current_scope=args.current_scope or None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
