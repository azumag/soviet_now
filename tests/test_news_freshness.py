#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
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
        "summary": "summary",
        "source": "Test News",
        "author": "",
        "license": "test",
        "lang": "ja",
        "source_key": "source",
        "published_ts": published_ts,
        "published_at": "2026-08-29T00:00:00Z",
    }


class NewsFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.old_paths = (fetch_news.PAST_NEWS, fetch_news.PAST_NEWS_LINK_HASHES)
        fetch_news.PAST_NEWS = "/dev/null"
        fetch_news.PAST_NEWS_LINK_HASHES = "/dev/null"
        fetch_news.NEWS_MAX_AGE_HOURS = 48

    def tearDown(self):
        fetch_news.PAST_NEWS, fetch_news.PAST_NEWS_LINK_HASHES = self.old_paths

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

    def test_google_news_sources_are_available_as_verified_fallback(self):
        google_sources = [source for source in fetch_news.SOURCES if source["key"].startswith("google_news_")]
        self.assertGreaterEqual(len(google_sources), 5)
        self.assertTrue(all("news.google.com/rss" in source["url"] for source in google_sources))

    def test_fetch_does_not_mark_every_candidate_as_read(self):
        attrs = {
            name: getattr(fetch_news, name)
            for name in (
                "TMP_DIR", "TMP_HISTORY_DIR", "TMP_STATE_DIR", "OUTFILE", "META_OUTFILE",
                "PAST_NEWS", "PAST_NEWS_LINK_HASHES", "LAST_NEWS_CACHE",
                "LAST_NEWS_META_CACHE", "FETCH_STATUS_FILE", "SOURCES", "fetch_source_items",
            )
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                history = root / "history"
                state = root / "state"
                fetch_news.TMP_DIR = str(root)
                fetch_news.TMP_HISTORY_DIR = str(history)
                fetch_news.TMP_STATE_DIR = str(state)
                fetch_news.OUTFILE = str(root / "news.txt")
                fetch_news.META_OUTFILE = str(root / "news_meta.json")
                fetch_news.PAST_NEWS = str(history / "past_news_read.txt")
                fetch_news.PAST_NEWS_LINK_HASHES = str(history / "past_news_url_hashes.txt")
                fetch_news.LAST_NEWS_CACHE = str(state / ".news_last_success.txt")
                fetch_news.LAST_NEWS_META_CACHE = str(state / ".news_last_success_meta.json")
                fetch_news.FETCH_STATUS_FILE = str(state / ".news_fetch_status.json")
                fetch_news.SOURCES = [{"key": "source"}]
                fetch_news.fetch_source_items = lambda _source: [item("not-yet-read", int(time.time()))]

                self.assertEqual(fetch_news.main(), 0)
                self.assertTrue(pathlib.Path(fetch_news.OUTFILE).is_file())
                self.assertFalse(pathlib.Path(fetch_news.PAST_NEWS).exists())
                self.assertFalse(pathlib.Path(fetch_news.PAST_NEWS_LINK_HASHES).exists())
        finally:
            for name, value in attrs.items():
                setattr(fetch_news, name, value)


if __name__ == "__main__":
    unittest.main()
