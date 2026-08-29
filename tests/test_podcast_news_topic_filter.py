#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("podcast_build", ROOT / "tools" / "podcast_build.py")
podcast_build = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(podcast_build)


class PodcastNewsTopicFilterTest(unittest.TestCase):
    def test_excludes_entertainment_product_and_sports_news(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            excluded = {
                "entertainment": "現在時刻は9時です。\n本日のニュースです。\nベネチア国際映画祭が開幕しました。",
                "product": "現在時刻は9時です。\n本日のニュースです。\nAppleが秋の新製品発表イベントを開催します。",
                "sports": "現在時刻は9時です。\n本日のニュースです。\n全米オープンテニスが開幕します。",
            }
            for name, text in excluded.items():
                path = root / f"radio_1_news_{name}.txt"
                path.write_text(text, encoding="utf-8")
                with self.subTest(name=name):
                    self.assertTrue(podcast_build._exclude_news_source_from_podcast(path))

    def test_keeps_public_affairs_news(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "radio_1_news_public.txt"
            path.write_text(
                "現在時刻は9時です。\n本日のニュースです。\n政府が物価高対策を国会へ提出しました。",
                encoding="utf-8",
            )
            self.assertFalse(podcast_build._exclude_news_source_from_podcast(path))


if __name__ == "__main__":
    unittest.main()
