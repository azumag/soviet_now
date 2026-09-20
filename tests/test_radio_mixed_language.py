#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.radio_mixed_language import find_spans, validate_replacement  # noqa: E402


class RadioMixedLanguageTests(unittest.TestCase):
    def test_finds_only_the_foreign_language_sentence(self) -> None:
        text = (
            "こんばんは。"
            "A bridge is more than just a structure. "
            "地元の人々は思い出を語っています。"
            "NATOの対応も注目です。"
        )

        spans = find_spans(text, "news")

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["text"], "A bridge is more than just a structure.")
        self.assertEqual(spans[0]["before"], "こんばんは。")
        self.assertIn("地元の人々", str(spans[0]["after"]))

    def test_allowed_acronym_does_not_create_a_span(self) -> None:
        text = "NATOとEUの対応を確認しながら、今後の動きを見ていきます。"

        self.assertEqual(find_spans(text, "news"), [])

    def test_news_lowercase_word_is_localized_to_its_sentence(self) -> None:
        text = (
            "政府の対応が誰の利益をserveしているのかが問われています。"
            "これは制度の問題です。"
        )

        spans = find_spans(text, "news")

        self.assertEqual(len(spans), 1)
        self.assertIn("serve", str(spans[0]["text"]))

    def test_replacement_must_preserve_numbers_and_urls(self) -> None:
        original = "2026年の発表は https://example.test/report を参照してください。"

        valid, reason = validate_replacement(
            original,
            "2026年の発表は https://example.test/report を参照してください。",
        )
        self.assertTrue(valid, reason)

        valid, reason = validate_replacement(
            original,
            "2025年の発表は https://example.test/report を参照してください。",
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "numbers_changed")

        valid, reason = validate_replacement(
            original,
            "2026年の発表内容を確認してください。",
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "urls_changed")


if __name__ == "__main__":
    unittest.main()
