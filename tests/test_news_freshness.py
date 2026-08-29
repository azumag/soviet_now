#!/usr/bin/env python3
import importlib.util
import pathlib
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fetch_news", ROOT / "lib" / "fetch_news.py")
fetch_news = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fetch_news)


def item(title: str, published_ts: int) -> dict:
    return {
        "title": title,
        "url": f"https://example.test/{title}",
        "published_ts": published_ts,
    }


class NewsFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.old_paths = (fetch_news.PAST_NEWS, fetch_news.PAST_NEWS_LINKS, fetch_news.PAST_NEWS_LINK_HASHES)
        fetch_news.PAST_NEWS = "/dev/null"
        fetch_news.PAST_NEWS_LINKS = "/dev/null"
        fetch_news.PAST_NEWS_LINK_HASHES = "/dev/null"
        fetch_news.NEWS_MAX_AGE_HOURS = 72

    def tearDown(self):
        fetch_news.PAST_NEWS, fetch_news.PAST_NEWS_LINKS, fetch_news.PAST_NEWS_LINK_HASHES = self.old_paths

    def test_rejects_stale_and_unknown_dates(self):
        now = int(time.time())
        source_key = fetch_news.SOURCES[0]["key"]
        candidates, stats = fetch_news.dedupe_candidates({
            source_key: [
                item("fresh", now - 2 * 3600),
                item("last-year", now - 365 * 24 * 3600),
                item("unknown-date", 0),
            ]
        })

        self.assertEqual([entry["title"] for entry in candidates[source_key]], ["fresh"])
        self.assertEqual(stats["overall"]["passed"], 1)
        self.assertEqual(stats["overall"]["stale_published_at"], 1)
        self.assertEqual(stats["overall"]["missing_published_at"], 1)


if __name__ == "__main__":
    unittest.main()
