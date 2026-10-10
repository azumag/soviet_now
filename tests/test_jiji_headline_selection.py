#!/usr/bin/env python3
"""時事コーナーの見出し取得が俗っぽい見出しを先頭に置かないことの回帰テスト。"""
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import fetch_google_headlines as fgh  # noqa: E402


def _rss(*titles):
    items = "".join(
        f"<item><title>{t}</title><link>https://example.com/{i}</link></item>"
        for i, t in enumerate(titles)
    )
    return f"<rss><channel>{items}</channel></rss>"


class JijiHeadlineSelectionTest(unittest.TestCase):
    def test_general_japan_top_feed_is_not_used(self):
        self.assertNotIn("jp", fgh.RSS_URLS)
        self.assertFalse(any("MDNfM2Q" in url for url in fgh.RSS_URLS.values()))

    def test_lurid_and_incident_only_headlines_are_dropped_and_feeds_interleave(self):
        feeds = {
            "jp_politics": _rss(
                "《愛人会社に政治資金8000万円》重大問題 - 文春オンライン",
                "首相G7欠席に野党から苦言続出 - 共同通信",
                "大阪都構想の特別区名、維新が各2案に絞り込み - 日本経済新聞",
            ),
            "jp_biz": _rss(
                "鮮魚売り場に並ぶサンマに“異変” - Sirabee",
                "日銀が政策金利を据え置き - NHKニュース",
            ),
            "jp_wrld": _rss(
                "酔っぱらい虚偽の110番 容疑で書類送検 - 神戸新聞",
                "尖閣諸島沖の領海に中国海警局の船が侵入 - 読売新聞",
            ),
        }
        urls = {url: feeds.get(name, _rss()) for name, url in fgh.RSS_URLS.items()}
        with mock.patch.object(fgh, "http_get", side_effect=lambda url, timeout=10.0: urls[url]):
            titles = [t for t, _link in fgh.fetch_headlines()]
        self.assertEqual(
            titles,
            [
                "首相G7欠席に野党から苦言続出 - 共同通信",
                "日銀が政策金利を据え置き - NHKニュース",
                "尖閣諸島沖の領海に中国海警局の船が侵入 - 読売新聞",
                "大阪都構想の特別区名、維新が各2案に絞り込み - 日本経済新聞",
            ],
        )


if __name__ == "__main__":
    unittest.main()
