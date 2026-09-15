#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import pathlib
import random
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("news_priority", ROOT / "lib" / "news_priority.py")
assert SPEC and SPEC.loader
news_priority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(news_priority)


class _RecordingRng:
    def __init__(self) -> None:
        self.population = None
        self.weights = None

    def random(self) -> float:
        return 0.0

    def choices(self, population, *, weights, k):
        self.population = list(population)
        self.weights = list(weights)
        self.assert_k = k
        return [self.population[0]]


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

    def test_source_history_uses_only_legacy_last_twelve_entries(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            path = handle.name
            handle.write("wikinews\n")
            handle.write("google_news\n" * 12)
        try:
            counts = news_priority._read_source_counts(path)
        finally:
            os.unlink(path)
        self.assertNotIn("wikinews", counts)
        self.assertEqual(counts, {"google_news": 12})

    def test_source_family_mapping_matches_legacy_picker(self) -> None:
        self.assertEqual(news_priority._name_to_key("ウィキニュース"), "wikinews")
        self.assertEqual(news_priority._name_to_key("Wikinews(en)"), "wikinews")
        self.assertEqual(news_priority._name_to_key("Google News 日本"), "google_news")
        self.assertEqual(news_priority._name_to_key("Global Voices"), "globalvoices")
        self.assertEqual(news_priority._name_to_key("NHK 政治"), "")

    def test_within_lane_weights_match_legacy_recency_and_source_formula(self) -> None:
        blocks = """■ 政府が予算案を発表\n本文A\n\n■ 首相が外交方針を説明\n本文B"""
        meta = {
            "政府が予算案を発表": {
                "source": "Google News 政治",
                "source_key": "google_news_jp_politics",
                "published_ts": 100000,
            },
            "首相が外交方針を説明": {
                "source": "Global Voices",
                "source_key": "globalvoices_ja",
                "published_ts": 56800,
            },
        }
        rng = _RecordingRng()
        news_priority.choose_news_block(
            blocks,
            meta=meta,
            source_counts={"google_news": 3, "globalvoices": 1},
            political_share=1.0,
            rng=rng,
        )
        self.assertEqual(rng.population, [0, 1])
        self.assertIsNotNone(rng.weights)
        self.assertAlmostEqual(rng.weights[0], 6.25)
        self.assertAlmostEqual(rng.weights[1], 3.5)

    def test_unknown_publish_time_keeps_legacy_quarter_recency_weight(self) -> None:
        blocks = news_priority._parse_blocks("■ 政府が法案を提出\n本文A\n\n■ 国会で審議継続\n本文B")
        meta = {
            "政府が法案を提出": {
                "source": "Google News 政治",
                "published_ts": 100000,
            },
            "国会で審議継続": {
                "source": "Global Voices",
            },
        }
        weights = news_priority._legacy_weights(
            blocks,
            meta=meta,
            source_counts={"google_news": 0, "globalvoices": 0},
        )
        self.assertAlmostEqual(weights[0], 7.0)
        self.assertAlmostEqual(weights[1], 2.5)


if __name__ == "__main__":
    unittest.main()
