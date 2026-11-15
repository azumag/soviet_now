"""dociai の告知 (announcement) を返信対象として分類する契約のテスト。

- 取り込み時の flags=announcement を分類結果へ反映する。
- trusted-card はカード返信の card_gacha を維持する。
- announcement 用プロンプトが存在し、共通の self_chat_context を参照する。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMENT_SH = REPO_ROOT / "broadcast/comment.sh"
ANNOUNCEMENT_PROMPT = REPO_ROOT / "prompts/comment_response_announcement.md"

LOADER = """
set -u
COMMENT_SH="$1"; shift
eval "$(sed -n '/^_apply_announcement_classification()/,/^}/p' "$COMMENT_SH")"
_apply_announcement_classification "$1" "$2"
"""


class AnnouncementClassificationTests(unittest.TestCase):
    def _run(self, rows, metadata):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            metadata_file = temp / "meta.jsonl"
            metadata_file.write_text(
                "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in metadata),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "-c", LOADER, "_", str(COMMENT_SH), json.dumps(rows), str(metadata_file)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_announcement_flag_overrides_classifier_category(self):
        rows = [
            {"index": 1, "user": "dociai", "comment": "第15次遠征は終了しました。", "category": "chitchat"},
            {"index": 2, "user": "alice", "comment": "こんにちは", "category": "chitchat"},
        ]
        metadata = [
            {"login": "dociai", "flags": ["self", "announcement"]},
            {"login": "alice", "flags": []},
        ]
        out = self._run(rows, metadata)
        self.assertEqual(out[0]["category"], "announcement")
        self.assertEqual(out[1]["category"], "chitchat")

    def test_trusted_card_keeps_card_gacha(self):
        rows = [{"index": 1, "user": "dociai", "comment": "alice が【レア】カード を獲得しました", "category": "card_gacha"}]
        metadata = [{"login": "dociai", "flags": ["self", "trusted-card", "announcement"]}]
        out = self._run(rows, metadata)
        self.assertEqual(out[0]["category"], "card_gacha")

    def test_prediction_announcement_becomes_announcement(self):
        rows = [{"index": 1, "user": "dociai", "comment": "チャネルポイント予想スタート！", "category": "chitchat"}]
        metadata = [{"login": "dociai", "flags": ["self", "trusted-prediction", "announcement"]}]
        out = self._run(rows, metadata)
        self.assertEqual(out[0]["category"], "announcement")

    def test_call_site_uses_prompt_batch_metadata_sidecar(self):
        # reply metadata は prompt batch の sidecar へコピーされた後に削除される。
        # 削除済みファイルを渡すと上書きが no-op になる回帰を防ぐ。
        text = COMMENT_SH.read_text(encoding="utf-8")
        self.assertIn(
            '_apply_announcement_classification "$classification_json" "$(_comment_batch_metadata_path "$comment_prompt_batch_file")"',
            text,
        )

    def test_missing_metadata_is_a_noop(self):
        rows = [{"index": 1, "user": "alice", "comment": "こんにちは", "category": "chitchat"}]
        with tempfile.TemporaryDirectory() as td:
            metadata_file = Path(td) / "missing.jsonl"
            result = subprocess.run(
                ["bash", "-c", LOADER, "_", str(COMMENT_SH), json.dumps(rows), str(metadata_file)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), rows)


class AnnouncementPromptTests(unittest.TestCase):
    def test_prompt_exists_and_is_wired_to_self_context(self):
        text = ANNOUNCEMENT_PROMPT.read_text(encoding="utf-8")
        self.assertIn("Category: Announcement", text)
        self.assertIn("${self_chat_context}", text)
        self.assertIn("first-person voice", text)
        self.assertIn("Never say", text)


if __name__ == "__main__":
    unittest.main()
