import datetime
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "podcast_build_title_test", ROOT / "tools" / "podcast_build.py"
)
podcast = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(podcast)


class PodcastTitleVarietyTest(unittest.TestCase):
    def test_detects_the_actual_consecutive_titles_as_similar(self):
        previous = "揺らぐ世界で問われる連帯と暮らし"
        current = "揺れる世界で問われる命と責任"
        self.assertGreaterEqual(podcast._title_similarity(previous, current), 0.55)
        self.assertTrue(podcast._title_too_similar(current, [previous]))
        self.assertFalse(podcast._title_too_similar("中東危機から読む難民支援", [previous]))

    def test_loads_only_older_recent_titles_in_newest_first_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for day, title in (
                ("2026-08-24", "古い題"),
                ("2026-08-25", "前日の題"),
                ("2026-08-26", "対象日の題"),
                ("2026-08-27", "未来の題"),
            ):
                (out / f"{day}.meta.json").write_text(
                    json.dumps({"title": title}, ensure_ascii=False), encoding="utf-8"
                )
            (out / "broken.meta.json").write_text("{", encoding="utf-8")
            (out / "2026-08-23.meta.json").write_text("[]", encoding="utf-8")

            titles = podcast.recent_episode_titles(
                out, datetime.date(2026, 8, 26), limit=2
            )
            self.assertEqual(titles, ["前日の題", "古い題"])

    def test_retries_when_generated_title_is_too_similar(self):
        previous = "揺らぐ世界で問われる連帯と暮らし"
        responses = iter(
            [
                {
                    "title": "揺れる世界で問われる命と責任",
                    "summary": "最初の案",
                    "sections": [{"heading": "中東危機", "topics": [1, 2]}],
                },
                {
                    "title": "中東危機から読む難民支援",
                    "summary": "作り直した案",
                    "sections": [{"heading": "中東危機", "topics": [1, 2]}],
                },
            ]
        )
        prompts = []
        original = podcast.llm_generate

        def fake_generate(prompt, schema=None):
            prompts.append(prompt)
            return next(responses)

        podcast.llm_generate = fake_generate
        try:
            plan = podcast.plan_sections(
                [
                    {"n": 1, "topic": "中東", "points": "要点"},
                    {"n": 2, "topic": "難民", "points": "要点"},
                ],
                datetime.date(2026, 8, 26),
                recent_titles=[previous],
            )
        finally:
            podcast.llm_generate = original

        self.assertEqual(plan["title"], "中東危機から読む難民支援")
        self.assertEqual(len(prompts), 2)
        self.assertIn(previous, prompts[0])
        self.assertIn("揺れる世界で問われる命と責任", prompts[1])

    def test_uses_specific_section_fallback_after_three_similar_titles(self):
        previous = "揺らぐ世界で問われる連帯と暮らし"
        calls = 0
        original = podcast.llm_generate

        def fake_generate(prompt, schema=None):
            nonlocal calls
            calls += 1
            return {
                "title": "揺れる世界で問われる命と責任",
                "summary": "類似案",
                "sections": [
                    {"heading": "中東危機", "topics": [1]},
                    {"heading": "難民支援", "topics": [2]},
                ],
            }

        podcast.llm_generate = fake_generate
        try:
            plan = podcast.plan_sections(
                [
                    {"n": 1, "topic": "中東", "points": "要点"},
                    {"n": 2, "topic": "難民", "points": "要点"},
                ],
                datetime.date(2026, 8, 26),
                recent_titles=[previous],
            )
        finally:
            podcast.llm_generate = original

        self.assertEqual(calls, 3)
        self.assertEqual(plan["title"], "中東危機から読む難民支援")
        self.assertFalse(podcast._title_too_similar(plan["title"], [previous]))


if __name__ == "__main__":
    unittest.main()
