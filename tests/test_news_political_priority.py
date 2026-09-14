#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import pathlib
import random
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("news_priority", ROOT / "lib" / "news_priority.py")
assert SPEC and SPEC.loader
news_priority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(news_priority)


class NewsPoliticalPriorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.blocks = """■ 国会で来年度予算案を審議\n政治本文\n\n■ 大手メーカーが新工場を稼働\n経済本文"""
        self.meta = {
            "国会で来年度予算案を審議": {
                "source": "NHK 政治",
                "source_key": "nhk_politics",
                "published_at": "2026-09-14T10:00:00Z",
            },
            "大手メーカーが新工場を稼働": {
                "source": "Google News 日本経済",
                "source_key": "google_news_jp_business",
                "published_at": "2026-09-14T10:00:00Z",
            },
        }

    def test_default_share_is_two_thirds_target(self) -> None:
        self.assertEqual(news_priority.DEFAULT_POLITICAL_SHARE, 0.67)

    def test_political_source_marks_even_neutral_headline(self) -> None:
        self.assertTrue(news_priority.is_political_title("きょうの主な動き", "nhk_politics"))

    def test_policy_and_diplomacy_terms_are_political(self) -> None:
        self.assertTrue(news_priority.is_political_title("政府が減税法案を国会に提出"))
        self.assertTrue(news_priority.is_political_title("首脳会談で安全保障を協議"))
        self.assertFalse(news_priority.is_political_title("大手メーカーが新工場を稼働"))

    def test_share_one_always_uses_political_lane_when_both_exist(self) -> None:
        chosen = news_priority.choose_news_block(
            self.blocks,
            meta=self.meta,
            source_counts={},
            political_share=1.0,
            rng=random.Random(1),
        )
        self.assertTrue(chosen.startswith("■ 国会で来年度予算案を審議"))

    def test_share_zero_uses_other_lane_when_both_exist(self) -> None:
        chosen = news_priority.choose_news_block(
            self.blocks,
            meta=self.meta,
            source_counts={},
            political_share=0.0,
            rng=random.Random(1),
        )
        self.assertTrue(chosen.startswith("■ 大手メーカーが新工場を稼働"))

    def test_lane_fallback_works_when_only_other_news_exists(self) -> None:
        other_only = "■ 大手メーカーが新工場を稼働\n経済本文"
        chosen = news_priority.choose_news_block(
            other_only,
            meta=self.meta,
            source_counts={},
            political_share=1.0,
            rng=random.Random(1),
        )
        self.assertEqual(chosen, other_only)

    def test_invalid_env_falls_back_and_values_are_clamped(self) -> None:
        previous = os.environ.get("NEWS_POLITICAL_SHARE")
        try:
            os.environ["NEWS_POLITICAL_SHARE"] = "invalid"
            self.assertEqual(news_priority.political_share_from_env(), 0.67)
            os.environ["NEWS_POLITICAL_SHARE"] = "4"
            self.assertEqual(news_priority.political_share_from_env(), 1.0)
            os.environ["NEWS_POLITICAL_SHARE"] = "-1"
            self.assertEqual(news_priority.political_share_from_env(), 0.0)
        finally:
            if previous is None:
                os.environ.pop("NEWS_POLITICAL_SHARE", None)
            else:
                os.environ["NEWS_POLITICAL_SHARE"] = previous


if __name__ == "__main__":
    unittest.main()
