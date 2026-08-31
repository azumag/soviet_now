import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "podcast_video_chapter_test", ROOT / "tools" / "podcast_video_build.py"
)
podcast_video = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(podcast_video)


class PodcastVideoChapterTimestampTest(unittest.TestCase):
    def test_formats_sub_hour_timestamp_as_minutes_and_seconds(self):
        self.assertEqual(podcast_video.format_youtube_timestamp(3303), "55:03")

    def test_formats_hour_timestamp_with_hour_field(self):
        self.assertEqual(podcast_video.format_youtube_timestamp(3705), "1:01:45")
        self.assertEqual(podcast_video.format_youtube_timestamp(6680), "1:51:20")

    def test_rejects_negative_timestamp(self):
        with self.assertRaises(ValueError):
            podcast_video.format_youtube_timestamp(-1)

    def test_description_uses_hour_field_after_one_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            chapters = Path(tmp) / "chapters.json"
            chapters.write_text(
                json.dumps(
                    {
                        "chapters": [
                            {"startTime": 3303, "title": "一時間前"},
                            {"startTime": 3705, "title": "一時間後"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            description = podcast_video.build_description(
                {"summary": "要約"}, chapters
            )
        self.assertIn("55:03 一時間前", description)
        self.assertIn("1:01:45 一時間後", description)
        self.assertNotIn("61:45 一時間後", description)


if __name__ == "__main__":
    unittest.main()
