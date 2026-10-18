import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
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

    def viewer_fields(self, root: Path) -> dict[str, str]:
        lines = (root / "prompts/viewer_title.md").read_text(encoding="utf-8").splitlines()
        fields: dict[str, str] = {}
        for line in lines:
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        return fields

    def test_extracts_explicit_candidate_metadata_from_latest_section(self):
        root = self.run_builder(
            """## 2026-10-18 JST — PR770をmainへマージ
- 視聴者向けタイトル: AIの取引コーナーからゲームへ、画面切替を改善
- 根拠: 画面復帰を運用で確認
- 確認段階: verified
- 生成日時: 2026-10-18T15:00:00Z
- 有効範囲: NetHack

## 2026-10-17 JST — old
- 視聴者向けタイトル: 古い公開タイトル
"""
        )
        fields = self.viewer_fields(root)
        self.assertEqual(fields["viewer_title"], "AIの取引コーナーからゲームへ、画面切替を改善")
        self.assertEqual(fields["rationale"], "画面復帰を運用で確認")
        self.assertEqual(fields["status"], "verified")
        self.assertEqual(fields["generated_at"], "2026-10-18T15:00:00Z")
        self.assertEqual(fields["valid_scope"], "NetHack")
        self.assertNotIn("古い公開タイトル", "\n".join(fields.values()))

    def test_defaults_are_safe_when_metadata_is_omitted(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        root = self.run_builder(
            """## 2026-10-18 JST — 内部向けの作業見出し
- 視聴者向けタイトル: NetHackでAIが長期攻略に挑戦
"""
        )
        fields = self.viewer_fields(root)
        self.assertEqual(fields["status"], "implemented")
        generated = datetime.fromisoformat(fields["generated_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(generated, before)
        self.assertNotIn("rationale", fields)
        self.assertNotIn("valid_scope", fields)

    def test_does_not_reuse_older_candidate_when_latest_has_none(self):
        root = self.run_builder(
            """## 2026-10-18 JST — 最新の内部作業
- internal only

## 2026-10-17 JST — old
- 視聴者向けタイトル: 古い公開タイトル
"""
        )
        viewer = (root / "prompts/viewer_title.md").read_text(encoding="utf-8")
        self.assertNotIn("古い公開タイトル", viewer)
        self.assertNotIn("viewer_title:", viewer)

    def test_ops_brief_still_contains_internal_topics(self):
        root = self.run_builder(
            """## 2026-10-18 JST — PR770をmainへマージ
- 視聴者向けタイトル: 一般向けタイトル

## 2026-10-17 JST — 別の改修
"""
        )
        brief = (root / "prompts/ops_brief.md").read_text(encoding="utf-8")
        self.assertIn("PR770をmainへマージ", brief)
        self.assertIn("別の改修", brief)


if __name__ == "__main__":
    unittest.main()
