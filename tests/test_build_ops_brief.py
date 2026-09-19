import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BuildOpsBriefTest(unittest.TestCase):
    def run_builder(self, handoff: str):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "tools").mkdir()
        (root / "prompts").mkdir()
        shutil.copy(ROOT / "tools/build_ops_brief.sh", root / "tools/build_ops_brief.sh")
        source = root / "handoff.md"
        source.write_text(handoff, encoding="utf-8")
        result = subprocess.run(
            ["bash", str(root / "tools/build_ops_brief.sh"), str(source)],
            cwd=root,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return root

    def test_extracts_explicit_viewer_title_from_latest_section(self):
        root = self.run_builder(
            """## 2026-09-20 JST — PR770をmainへマージ
- 視聴者向けタイトル: AIの取引コーナーからゲームへ、画面切替を改善
- internal: PR #770

## 2026-09-19 JST — old
- 視聴者向けタイトル: 古い公開タイトル
"""
        )
        viewer = (root / "prompts/viewer_title.md").read_text(encoding="utf-8")
        self.assertIn("- AIの取引コーナーからゲームへ、画面切替を改善", viewer)
        self.assertNotIn("古い公開タイトル", viewer)

    def test_does_not_reuse_older_title_when_latest_has_none(self):
        root = self.run_builder(
            """## 2026-09-20 JST — 最新の内部作業
- internal only

## 2026-09-19 JST — old
- viewer_title: 古い公開タイトル
"""
        )
        viewer = (root / "prompts/viewer_title.md").read_text(encoding="utf-8")
        self.assertNotIn("古い公開タイトル", viewer)
        self.assertNotIn("- ", viewer)

    def test_ops_brief_still_contains_internal_topics(self):
        root = self.run_builder(
            """## 2026-09-20 JST — PR770をmainへマージ
- viewer_title: 一般向けタイトル

## 2026-09-19 JST — 別の改修
"""
        )
        brief = (root / "prompts/ops_brief.md").read_text(encoding="utf-8")
        self.assertIn("PR770をmainへマージ", brief)
        self.assertIn("別の改修", brief)


if __name__ == "__main__":
    unittest.main()
