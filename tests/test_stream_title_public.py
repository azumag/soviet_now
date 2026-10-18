import tempfile
from pathlib import Path
import unittest

from lib.stream_title_public import (
    DEFAULT_FALLBACK,
    choose_body,
    compose_title,
    looks_internal,
)


class StreamTitlePublicTest(unittest.TestCase):
    def candidate_file(self, root: Path, body: str) -> str:
        path = root / "viewer_title.md"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_internal_development_logs_are_rejected(self):
        samples = [
            "PR770をmainへマージ",
            "PR #770 を main にマージ、VM反映と実機状態を確認",
            "docich#770 の参照更新",
            "5ec6fdd88909aa554e02859170b4d8d711be0db4 をデプロイ",
            "tests/test_title_day.py のCI成功",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(looks_internal(sample))

    def test_internal_logs_adjacent_to_japanese_are_rejected(self):
        # 助詞・漢字かながASCII識別子に直接隣接する実運用形式（空白なし）。
        # Unicode \b はこの境界で成立しないため、ASCII構成文字ベースの境界で検出する。
        samples = [
            "PR #899を統合・現行mainへVM反映、Snake復帰カテゴリのlive境界を確認",
            "PR770を統合",
            "docich#770の参照更新",
            "5ec6fdd88909aa554e02859170b4d8d711be0db4へデプロイ",
            "tests/test_title_day.pyのCI成功",
            "現行mainへ反映済み",
            "VM上で稼働中",
            "root v763 継続中の攻略",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(looks_internal(sample))

    def test_viewer_wording_is_not_flagged_internal(self):
        samples = [
            "AIの取引コーナーからゲームへ、画面切替を改善",
            "NetHackでAIが長期攻略に挑戦",
            "視聴者参加のAI探偵ミステリーコーナーを追加",
            "半熟英雄でAIが王国を運営",
            "ペーパートレード AIの検証配信",
            "ソ連ゲーム AIプレイ配信",
            "ゲームのmaintainではなく体験を改善する",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(looks_internal(sample))

    def test_compose_falls_back_on_unspaced_internal_activity(self):
        title = compose_title(
            day="249",
            activity="PR #899を統合・現行mainへVM反映",
            strategy="root v900 継続",
            candidate_file=None,
            fallback=None,
        )
        self.assertEqual(title, f"[day249] {DEFAULT_FALLBACK}")
        self.assertNotIn("899", title)
        self.assertNotIn("v900", title)

    def test_explicit_viewer_title_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(
                Path(d),
                "# generated\n- AIの取引コーナーからゲームへ、画面切替を改善\n",
            )
            self.assertEqual(
                choose_body(
                    current="[day190] 以前の一般向けタイトル",
                    candidate_file=path,
                    fallback=None,
                ),
                "AIの取引コーナーからゲームへ、画面切替を改善",
            )

    def test_safe_current_title_is_preserved_when_candidate_missing(self):
        self.assertEqual(
            choose_body(
                current="[day190] NetHackで長期攻略に挑戦",
                candidate_file="/definitely/missing",
                fallback=None,
            ),
            "NetHackで長期攻略に挑戦",
        )

    def test_internal_current_title_falls_back(self):
        self.assertEqual(
            choose_body(
                current="[day190] PR770をmainへマージ",
                candidate_file="/definitely/missing",
                fallback=None,
            ),
            DEFAULT_FALLBACK,
        )

    def test_internal_candidate_does_not_replace_safe_current(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), "- PR #770をmainへマージ\n")
            self.assertEqual(
                choose_body(
                    current="[day190] AIたちがゲームに挑戦中",
                    candidate_file=path,
                    fallback=None,
                ),
                "AIたちがゲームに挑戦中",
            )

    def test_compose_rejects_internal_activity_and_strategy(self):
        title = compose_title(
            day="190",
            activity="PR770をmainへマージ",
            strategy="root v763 継続",
            candidate_file=None,
            fallback=None,
        )
        self.assertEqual(title, f"[day190] {DEFAULT_FALLBACK}")
        self.assertNotIn("PR770", title)
        self.assertNotIn("v763", title)

    def test_compose_keeps_public_names_and_limit(self):
        title = compose_title(
            day="190",
            activity="NetHackでAIが長期攻略に挑戦 " + "あ" * 200,
            strategy="慎重に探索中",
            candidate_file=None,
            fallback=None,
        )
        self.assertTrue(title.startswith("[day190] NetHack"))
        self.assertIn("慎重に探索中", title)
        self.assertLessEqual(len(title), 140)
        self.assertNotIn("\n", title)


if __name__ == "__main__":
    unittest.main()
