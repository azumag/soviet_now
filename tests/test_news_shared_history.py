import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("news_filter", ROOT / "lib/news_filter.py")
news_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news_filter)


class SharedHistoryTests(unittest.TestCase):
    title = "政府が電気料金の補助金延長を決定"
    paraphrase = "電気代支援を継続へ 政府方針"

    def test_japanese_paraphrase(self):
        self.assertTrue(news_filter.same_event(
            news_filter.event_tokens(self.title), news_filter.event_tokens(self.paraphrase)))

    def test_substantive_followups_and_other_actors(self):
        for a, b in (
            (self.title, "政府が電気料金の補助金終了を決定"),
            (self.title, "政府が電気代支援を開始"),
            (self.title + " 10月まで", self.paraphrase + " 12月まで"),
            (self.title + " 1000円", self.paraphrase + " 2000円"),
            (self.title, "東京都が電気代支援を継続"),
            (self.title, "政府がガス料金の支援を継続"),
            (self.title, "米政府が電気料金の補助金延長を決定"),
            (self.title, "政府が電気料金の補助金延長を見送り"),
            (self.title, "政府が電気料金の補助金延長を否定"),
        ):
            with self.subTest(a=a, b=b):
                self.assertFalse(news_filter.same_event(
                    news_filter.event_tokens(a), news_filter.event_tokens(b)))

    def run_lane(self, lane, candidate, *, peer_title="", peer_key="", peer_url="",
                 own_exists=True, event_dedup="1"):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "tmp").mkdir()
            for name, text in (("peer_titles", peer_title), ("peer_keys", peer_key),
                               ("peer_urls", peer_url), ("own_keys", ""),
                               ("own_topics", ""), ("own_urls", "")):
                (cwd / name).write_text(text, encoding="utf-8")
            if own_exists:
                (cwd / "own_titles").touch()
            meta = {candidate: {"url": "https://example.test/article"}}
            (cwd / "tmp/news_meta.json").write_text(json.dumps(meta))
            (cwd / "candidate").write_text("■ " + candidate + "\n本文\n", encoding="utf-8")
            env = dict(os.environ, ELOOP_LIB_DIR=str(ROOT), NEWS_EVENT_DEDUP=event_dedup)
            if lane == "jiji":
                (cwd / "tmp/google_headlines_meta.json").write_text(json.dumps(meta))
                command = ["bash", "-c", '''
                    source "$ELOOP_LIB_DIR/broadcast/radio_corners.sh"
                    PAST_NEWS_READ=peer_titles PAST_NEWS_READ_KEYS=peer_keys
                    PAST_NEWS_URL_HASHES=peer_urls PAST_JIJI_URL_HASHES=own_urls
                    TMP_HISTORY_DIR=history
                    mkdir history
                    if [ -f own_titles ]; then cp own_titles history/.past_jiji_titles.txt; fi
                    cp own_keys history/.past_jiji_keys.txt
                    _filter_unread_jiji_blocks <candidate
                ''']
            else:
                # Execute the actual sourced news lane; don't duplicate its Python.
                command = ["bash", "-c", '''
                    source "$ELOOP_LIB_DIR/broadcast/radio_news.sh"
                    PAST_NEWS_READ=own_titles PAST_NEWS_READ_KEYS=own_keys
                    PAST_NEWS_TOPIC_KEYS=own_topics PAST_NEWS_URL_HASHES=own_urls
                    TMP_HISTORY_DIR=history PAST_JIJI_URL_HASHES=peer_urls
                    mkdir history
                    cp peer_titles history/.past_jiji_titles.txt
                    cp peer_keys history/.past_jiji_keys.txt
                    _filter_unread_news_blocks <candidate
                ''']
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                    text=True, check=True).stdout.strip()
            self.assertEqual((cwd / "peer_titles").read_text(), peer_title)
            return result

    def test_both_lanes_read_other_lane(self):
        for lane in ("news", "jiji"):
            for own_exists in (True, False):
                with self.subTest(lane=lane, own_exists=own_exists):
                    self.assertEqual(self.run_lane(lane, self.paraphrase,
                        peer_title=self.title, own_exists=own_exists), "")

    def test_exact_title_key_and_url_still_work_when_events_disabled(self):
        for lane in ("news", "jiji"):
            for kwargs in ({"peer_title": self.title},
                           {"peer_key": news_filter.key(self.title)},
                           {"peer_url": news_filter.url_hash("https://example.test/article")}):
                with self.subTest(lane=lane, kwargs=kwargs):
                    self.assertEqual(self.run_lane(lane, self.title,
                        event_dedup="0", **kwargs), "")

    def test_unrelated_and_followup_candidates_survive(self):
        for lane in ("news", "jiji"):
            for candidate in ("政府が電気料金の補助金終了を決定",
                              "政府が電気代支援を開始", "国会で選挙制度の改革案を審議"):
                with self.subTest(lane=lane, candidate=candidate):
                    self.assertTrue(self.run_lane(lane, candidate, peer_title=self.title))


if __name__ == "__main__":
    unittest.main()
