"""tools/podcast_publish.py: 同じ日付を二度上げない (重複アップロード防止)。

doci に触れる前で止まることを確かめる。ネットワークへは出さない。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import podcast_publish as pp  # noqa: E402


class PublishGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "2026-08-25.mp4").write_bytes(b"0" * 16)
        self.argv = sys.argv
        # 誤って本物の doci を掴まないよう、呼ばれたら失敗させる
        self.orig_find = pp.find_doci
        pp.find_doci = lambda: (_ for _ in ()).throw(
            AssertionError("公開済みなのに doci を呼んだ"))

    def tearDown(self):
        pp.find_doci = self.orig_find
        sys.argv = self.argv
        self.tmp.cleanup()

    def run_main(self, *args):
        sys.argv = ["podcast_publish.py", *args]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = pp.main()
        return rc, buf.getvalue()

    def test_skips_when_already_published(self):
        (self.out / "2026-08-25.publish.json").write_text(json.dumps(
            {"url": "https://www.youtube.com/watch?v=orig", "video_id": "orig"}),
            encoding="utf-8")
        rc, out = self.run_main("--date", "20260825", "--out-dir", str(self.out))
        self.assertEqual(rc, 0)
        self.assertIn("https://www.youtube.com/watch?v=orig", out)

    def test_force_goes_past_the_guard(self):
        (self.out / "2026-08-25.publish.json").write_text(json.dumps(
            {"url": "https://www.youtube.com/watch?v=orig"}), encoding="utf-8")
        with self.assertRaises(AssertionError):   # ガードを抜けて doci 探索まで進む
            self.run_main("--date", "20260825", "--out-dir", str(self.out), "--force")

    def test_missing_video_returns_2(self):
        rc, _ = self.run_main("--date", "20260824", "--out-dir", str(self.out))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
