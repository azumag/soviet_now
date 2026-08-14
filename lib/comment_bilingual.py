#!/usr/bin/env python3
"""Detect English viewer comments and prepare ordered bilingual speech segments."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ENGLISH_MARKER = "===ENGLISH==="
JAPANESE_MARKER = "===JAPANESE==="
END_MARKER = "===END_BILINGUAL==="
MARKERS = {ENGLISH_MARKER, JAPANESE_MARKER, END_MARKER}

LATIN_RE = re.compile(r"[A-Za-z]")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
LEADING_TAG_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
SHORT_ENGLISH_MESSAGES = {
    "gg",
    "gl",
    "hf",
    "hi",
    "no",
    "ok",
    "ty",
    "wp",
    "yo",
}


class BilingualFormatError(ValueError):
    """Raised when a required bilingual response is malformed."""


def extract_comment_body(line: str) -> str:
    """Remove queue metadata and the viewer name before language detection."""
    cleaned = line.strip()
    if cleaned.startswith("id=") and "\t" in cleaned:
        cleaned = cleaned.split("\t", 1)[1]
    cleaned = LEADING_TAG_RE.sub("", cleaned)
    if ": " in cleaned:
        cleaned = cleaned.split(": ", 1)[1]
    return URL_RE.sub("", cleaned).strip()


def looks_like_english(text: str) -> bool:
    """Conservatively identify English without treating Latin usernames as text."""
    cleaned = URL_RE.sub("", text)
    latin_count = len(LATIN_RE.findall(cleaned))
    japanese_count = len(JAPANESE_RE.findall(cleaned))
    cyrillic_count = len(CYRILLIC_RE.findall(cleaned))
    if japanese_count or cyrillic_count:
        return False
    if latin_count >= 3:
        return True
    tokens = re.findall(r"[A-Za-z]+", cleaned.lower())
    return len(tokens) == 1 and tokens[0] in SHORT_ENGLISH_MESSAGES


def batch_has_english(lines: list[str]) -> bool:
    return any(looks_like_english(extract_comment_body(line)) for line in lines)


def count_english_comments(lines: list[str]) -> int:
    return sum(looks_like_english(extract_comment_body(line)) for line in lines)


def _normalized_block(lines: list[str], label: str) -> str:
    text = "\n".join(lines).strip()
    if not text:
        raise BilingualFormatError(f"{label} block is empty")
    return re.sub(r"\n{3,}", "\n\n", text)


def _validate_english_block(text: str) -> None:
    if len(LATIN_RE.findall(text)) < 3:
        raise BilingualFormatError("English block does not contain English text")
    if JAPANESE_RE.search(text):
        raise BilingualFormatError("English block contains Japanese text")


def _validate_japanese_block(text: str) -> None:
    if not JAPANESE_RE.search(text):
        raise BilingualFormatError("Japanese block does not contain Japanese text")


def _strip_exact_duplicate_english_before_markers(text: str) -> str:
    """Remove only an exact English-block duplicate immediately before its marker.

    MiniMax can occasionally emit the reply once and then repeat the same reply
    inside the required marker block. Arbitrary unmarked English must still fail.
    """
    search_from = 0
    while True:
        english_marker_index = text.find(ENGLISH_MARKER, search_from)
        if english_marker_index < 0:
            return text
        english_start = english_marker_index + len(ENGLISH_MARKER)
        japanese_marker_index = text.find(JAPANESE_MARKER, english_start)
        if japanese_marker_index < 0:
            return text

        english_text = text[english_start:japanese_marker_index].strip()
        before = text[:english_marker_index].rstrip()
        if english_text and before.endswith(english_text):
            duplicate_start = len(before) - len(english_text)
            if duplicate_start == 0 or before[duplicate_start - 1] == "\n":
                kept = before[:duplicate_start].rstrip()
                separator = "\n\n" if kept else ""
                text = kept + separator + text[english_marker_index:]
                search_from = len(kept) + len(separator) + len(ENGLISH_MARKER)
                continue

        search_from = japanese_marker_index + len(JAPANESE_MARKER)


def parse_bilingual_response(
    text: str, expected_pairs: int | None = None
) -> dict[str, object]:
    """Parse marker pairs while preserving plain Japanese replies around them."""
    text = _strip_exact_duplicate_english_before_markers(text)
    state = "outside"
    buffer: list[str] = []
    english_text = ""
    segments: list[dict[str, str]] = []
    pair_count = 0

    def flush_plain() -> None:
        nonlocal buffer
        plain = "\n".join(buffer).strip()
        buffer = []
        if not plain:
            return
        if any(
            looks_like_english(line.strip())
            for line in plain.splitlines()
            if line.strip()
        ):
            raise BilingualFormatError("English reply exists outside marker block")
        segments.append({"language": "ja", "role": "reply", "text": plain})

    for raw_line in text.splitlines():
        marker = raw_line.strip()
        if marker not in MARKERS:
            buffer.append(raw_line.rstrip())
            continue

        if marker == ENGLISH_MARKER:
            if state != "outside":
                raise BilingualFormatError("unexpected English marker")
            flush_plain()
            state = "english"
            continue

        if marker == JAPANESE_MARKER:
            if state != "english":
                raise BilingualFormatError("unexpected Japanese marker")
            english_text = _normalized_block(buffer, "English")
            _validate_english_block(english_text)
            buffer = []
            state = "japanese"
            continue

        if state != "japanese":
            raise BilingualFormatError("unexpected bilingual end marker")
        japanese_text = _normalized_block(buffer, "Japanese")
        _validate_japanese_block(japanese_text)
        buffer = []
        segments.append(
            {"language": "en", "role": "reply", "text": english_text}
        )
        segments.append(
            {"language": "ja", "role": "translation", "text": japanese_text}
        )
        pair_count += 1
        state = "outside"

    if state != "outside":
        raise BilingualFormatError("bilingual marker block is incomplete")
    flush_plain()
    if pair_count == 0:
        raise BilingualFormatError("no bilingual marker pair found")
    if expected_pairs is not None and pair_count != expected_pairs:
        raise BilingualFormatError(
            f"expected {expected_pairs} bilingual pair(s), found {pair_count}"
        )

    display_parts: list[str] = []
    for segment in segments:
        segment_text = segment["text"]
        if segment["role"] == "translation":
            display_parts.append(f"日本語訳：\n{segment_text}")
        else:
            display_parts.append(segment_text)

    return {
        "bilingual": True,
        "english_reply_count": pair_count,
        "speech_segments": segments,
        "display_text": "\n\n".join(display_parts).strip(),
    }


def load_speech_segments(metadata_path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BilingualFormatError(f"cannot read speech metadata: {exc}") from exc

    raw_segments = payload.get("speech_segments")
    if not payload.get("bilingual") or not isinstance(raw_segments, list):
        raise BilingualFormatError("metadata is not bilingual")

    segments: list[dict[str, str]] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise BilingualFormatError("speech segment is not an object")
        language = str(raw.get("language") or "")
        role = str(raw.get("role") or "reply")
        text = str(raw.get("text") or "").strip()
        if language not in {"en", "ja"} or not text:
            raise BilingualFormatError("speech segment has invalid language or text")
        segments.append({"language": language, "role": role, "text": text})
    if not segments or not any(segment["language"] == "en" for segment in segments):
        raise BilingualFormatError("metadata has no English speech segment")
    for index, segment in enumerate(segments):
        if segment["language"] == "en":
            if segment["role"] != "reply":
                raise BilingualFormatError("English segment has an invalid role")
            if index + 1 >= len(segments):
                raise BilingualFormatError("English segment has no Japanese translation")
            following = segments[index + 1]
            if following["language"] != "ja" or following["role"] != "translation":
                raise BilingualFormatError("English segment is not followed by Japanese")
        elif segment["role"] not in {"reply", "translation"}:
            raise BilingualFormatError("Japanese segment has an invalid role")
        elif segment["role"] == "translation" and (
            index == 0 or segments[index - 1]["language"] != "en"
        ):
            raise BilingualFormatError("Japanese translation has no English segment")
    return segments


def command_detect(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 2
    count = count_english_comments(lines)
    print(count)
    return 0 if count else 1


def command_parse(metadata_path: Path, expected_pairs: int | None) -> int:
    try:
        parsed = parse_bilingual_response(sys.stdin.read(), expected_pairs=expected_pairs)
        metadata_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(str(parsed["display_text"]))
    return 0


def command_emit_segments(metadata_path: Path, output_dir: Path) -> int:
    try:
        segments = load_speech_segments(metadata_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        for index, segment in enumerate(segments):
            segment_path = output_dir / f"{index:03d}_{segment['language']}.txt"
            segment_path.write_text(segment["text"] + "\n", encoding="utf-8")
            print(f"{segment['language']}\t{segment_path}")
    except (OSError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    return 0


def command_extract_language(metadata_path: Path, language: str) -> int:
    try:
        segments = load_speech_segments(metadata_path)
    except BilingualFormatError as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    selected = [segment["text"] for segment in segments if segment["language"] == language]
    if not selected:
        return 1
    sys.stdout.write("\n\n".join(selected))
    return 0


def command_has_speech(metadata_path: Path) -> int:
    try:
        load_speech_segments(metadata_path)
    except BilingualFormatError:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect")
    detect.add_argument("path", type=Path)

    parse = subparsers.add_parser("parse")
    parse.add_argument("--metadata", required=True, type=Path)
    parse.add_argument("--expected-pairs", type=int)

    emit = subparsers.add_parser("emit-segments")
    emit.add_argument("metadata", type=Path)
    emit.add_argument("output_dir", type=Path)

    extract = subparsers.add_parser("extract-language")
    extract.add_argument("metadata", type=Path)
    extract.add_argument("language", choices=("en", "ja"))

    has_speech = subparsers.add_parser("has-speech")
    has_speech.add_argument("metadata", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "detect":
        return command_detect(args.path)
    if args.command == "parse":
        return command_parse(args.metadata, args.expected_pairs)
    if args.command == "emit-segments":
        return command_emit_segments(args.metadata, args.output_dir)
    if args.command == "extract-language":
        return command_extract_language(args.metadata, args.language)
    if args.command == "has-speech":
        return command_has_speech(args.metadata)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
