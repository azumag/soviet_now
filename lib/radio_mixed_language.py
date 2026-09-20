#!/usr/bin/env python3
"""Locate and validate local repairs for mixed-language radio text.

The quality guard only needs a yes/no verdict, while the repair path needs a
small, stable unit of text to send back to a model.  This module deliberately
works at sentence/line boundaries so a repair never asks the model to rewrite
the whole radio script.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable


DEFAULT_ALLOWED_LATIN = {
    "afd",
    "ai",
    "ap",
    "apec",
    "asean",
    "bbc",
    "brics",
    "cnn",
    "cptpp",
    "df",
    "dx",
    "eu",
    "g7",
    "g20",
    "gdp",
    "iaea",
    "imf",
    "it",
    "jaxa",
    "lgbtq",
    "ms",
    "msnow",
    "nasa",
    "nato",
    "nhk",
    "npt",
    "oecd",
    "opec",
    "politico",
    "reuters",
    "rcep",
    "sbi",
    "sdd",
    "soren",
    "ssd",
    "tbs",
    "tpp",
    "uk",
    "un",
    "us",
    "who",
    "wto",
    "youtube",
    "youtuber",
    "openai",
    "chatgpt",
    "google",
    "apple",
    "amazon",
    "microsoft",
    "meta",
    "tiktok",
    "facebook",
    "instagram",
    "spacex",
    "starlink",
    "iphone",
    "deepseek",
    "minimax",
    "claude",
    "codex",
    "opencode",
    "voicevox",
}

SIMPLIFIED_CHARS = set(
    "这们时说过还边续么纸论题单东车见话请谢让认识记该谁吗呢"
    "关问间样为从发书标极级约结给统经员运达违远连进选适现应务动员华亲爱儿丽举习乡买"
    "长头电听门无类团图药难义术击处备复组织济产业严饭开观实际"
)

ENGLISH_RUN_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){4,}(?![A-Za-z])"
)
LATIN_WORD_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{3,})(?![A-Za-z])")
LOWER_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")
# Include ASCII sentence punctuation for English runs, but do not split URLs or
# decimal numbers.  Japanese prose still primarily uses the first alternative.
SENTENCE_BOUNDARY_RE = re.compile(r"(?:[。！？\n]+|(?<![\d.])[.!?]+(?=\s|$))")
NUMBER_RE = re.compile(r"[0-9０-９]+(?:[.,．，][0-9０-９]+)?[%％]?")
URL_RE = re.compile(r"https?://\S+")


def _allowed_latin(extra: str = "") -> set[str]:
    allowed = set(DEFAULT_ALLOWED_LATIN)
    for word in re.split(r"[\s,]+", extra or ""):
        word = word.strip()
        if word:
            allowed.add(word.casefold())
    return allowed


def _trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _sentence_slices(text: str) -> Iterable[tuple[int, int]]:
    start = 0
    for boundary in SENTENCE_BOUNDARY_RE.finditer(text):
        raw_start, raw_end = _trim_bounds(text, start, boundary.end())
        if raw_start < raw_end:
            yield raw_start, raw_end
        start = boundary.end()
    raw_start, raw_end = _trim_bounds(text, start, len(text))
    if raw_start < raw_end:
        yield raw_start, raw_end


def _lower_words(text: str) -> list[str]:
    return [
        word
        for word in LOWER_WORD_RE.findall(text)
        if word[0].islower() and len(word) >= 3
    ]


def _segment_is_mixed(
    segment: str,
    corner: str,
    allowed_latin: set[str],
    total_lower_words: int,
) -> bool:
    if ENGLISH_RUN_RE.search(segment):
        return True
    if any(char in SIMPLIFIED_CHARS for char in segment):
        return True

    latin_words = LATIN_WORD_RE.findall(segment)
    if corner in {"news", "jiji"} and any(
        word.casefold() not in allowed_latin and not word.isupper()
        for word in latin_words
    ):
        return True

    # Keep parity with the existing whole-text guard: a large number of
    # ordinary lower-case English words is suspicious even when no single
    # sentence contains five consecutive words.
    return total_lower_words >= 8 and bool(_lower_words(segment))


def find_spans(text: str, corner: str = "", extra_allowed_latin: str = "") -> list[dict[str, object]]:
    """Return sentence/line spans that contain mixed-language signals."""

    allowed_latin = _allowed_latin(extra_allowed_latin)
    total_lower_words = len(_lower_words(text))
    spans: list[dict[str, object]] = []
    for start, end in _sentence_slices(text):
        segment = text[start:end]
        if not _segment_is_mixed(segment, corner, allowed_latin, total_lower_words):
            continue
        spans.append(
            {
                "start": start,
                "end": end,
                "text": segment,
                "before": text[max(0, start - 80) : start],
                "after": text[end : min(len(text), end + 80)],
            }
        )
    return spans


def _normalized_tokens(pattern: re.Pattern[str], text: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFKC", text)
    return Counter(match.group(0) for match in pattern.finditer(normalized))


def validate_replacement(original: str, replacement: str) -> tuple[bool, str]:
    """Validate a local replacement without judging its final full-script quality."""

    replacement = replacement.strip()
    if not replacement:
        return False, "empty"
    minimum_length = max(2, min(8, len(original) // 4))
    if len(replacement) < minimum_length:
        return False, "replacement_too_short"
    if len(replacement) > max(600, len(original) * 4 + 120):
        return False, "replacement_too_long"
    if re.search(r"[\x00-\x08\x0e-\x1f\x7f]", replacement):
        return False, "control_character"
    if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", replacement):
        return False, "not_japanese"

    original_numbers = _normalized_tokens(NUMBER_RE, original)
    replacement_numbers = _normalized_tokens(NUMBER_RE, replacement)
    if original_numbers != replacement_numbers:
        return False, "numbers_changed"

    original_urls = Counter(URL_RE.findall(original))
    replacement_urls = Counter(URL_RE.findall(replacement))
    if original_urls != replacement_urls:
        return False, "urls_changed"

    return True, "ok"


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def _run_spans(args: argparse.Namespace) -> int:
    text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    spans = find_spans(text, args.corner, args.allowed_latin)
    if args.reverse:
        spans.reverse()
    if args.format == "json":
        print(json.dumps(spans, ensure_ascii=False, separators=(",", ":")))
        return 0
    for span in spans:
        print(
            "\t".join(
                [
                    str(span["start"]),
                    str(span["end"]),
                    _b64(str(span["text"])),
                    _b64(str(span["before"])),
                    _b64(str(span["after"])),
                ]
            )
        )
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    original = Path(args.original_file).read_text(encoding="utf-8", errors="replace")
    replacement = Path(args.replacement_file).read_text(encoding="utf-8", errors="replace")
    valid, reason = validate_replacement(original, replacement)
    if not valid:
        print(reason, file=sys.stderr)
        return 1
    return 0


def _run_replace(args: argparse.Namespace) -> int:
    text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    replacement = Path(args.replacement_file).read_text(encoding="utf-8", errors="replace").strip()
    if args.start < 0 or args.end < args.start or args.end > len(text):
        print("invalid_span", file=sys.stderr)
        return 2
    output = text[: args.start] + replacement + text[args.end :]
    Path(args.output_file).write_text(output, encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    spans = subparsers.add_parser("spans")
    spans.add_argument("--text-file", required=True)
    spans.add_argument("--corner", default="")
    spans.add_argument("--allowed-latin", default="")
    spans.add_argument("--format", choices=("tsv", "json"), default="tsv")
    spans.add_argument("--reverse", action="store_true")
    spans.set_defaults(handler=_run_spans)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--original-file", required=True)
    validate.add_argument("--replacement-file", required=True)
    validate.set_defaults(handler=_run_validate)

    replace = subparsers.add_parser("replace")
    replace.add_argument("--text-file", required=True)
    replace.add_argument("--start", type=int, required=True)
    replace.add_argument("--end", type=int, required=True)
    replace.add_argument("--replacement-file", required=True)
    replace.add_argument("--output-file", required=True)
    replace.set_defaults(handler=_run_replace)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
