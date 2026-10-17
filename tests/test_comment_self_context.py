"""#2373: 自分の投稿を返信対象から分離し、自分の既発言として提示する契約。

broadcast/comment.sh の `_split_self_comment_batch` / `_build_self_chat_context` を
実際に bash で読み込んで検証する。判定は取り込み側が付けた identity metadata の
`flags=self` だけで、表示名・本文の一致では行わない（なりすまし対策）。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMENT_SH = REPO_ROOT / "broadcast/comment.sh"

LOADER = """
set -u
COMMENT_SH="$1"; shift
eval "$(sed -n '/^_split_self_comment_batch()/,/^}/p' "$COMMENT_SH")"
eval "$(sed -n '/^_build_self_chat_context()/,/^}/p' "$COMMENT_SH")"
_split_self_comment_batch "$@"
_build_self_chat_context "$5" "$6"
"""


def _entry(index: int, line: str, *, login: str = "", display: str = "", flags=None) -> dict:
    return {
        "line_index": index,
        "line": line,
        "source": "twitch",
        "message_id": f"msg-{index}",
        "stable_id": f"uid-{index}",
        "login": login,
        "display_name": display or login,
        "flags": list(flags or []),
    }


class CommentSelfContextTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp = Path(self._tempdir.name)

    def tearDown(self):
        self._tempdir.cleanup()

    def _run(self, lines, entries):
        batch = self.temp / "batch.txt"
        metadata = self.temp / "batch.viewer_meta.jsonl"
        reply = self.temp / "reply.txt"
        reply_meta = self.temp / "reply.viewer_meta.jsonl"
        self_file = self.temp / "self.txt"
        self_meta = self.temp / "self.viewer_meta.jsonl"
        batch.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        if entries is None:
            metadata.unlink(missing_ok=True)
        else:
            metadata.write_text(
                "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries),
                encoding="utf-8",
            )
        for path in (reply, reply_meta, self_file, self_meta):
            path.write_text("", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", LOADER, "_", str(COMMENT_SH), str(batch), str(metadata),
             str(reply), str(reply_meta), str(self_file), str(self_meta)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        def read(path):
            return path.read_text(encoding="utf-8").splitlines()

        def read_meta(path):
            return [json.loads(line) for line in read(path)] if path.exists() else []

        return {
            "reply": read(reply),
            "reply_meta": read_meta(reply_meta),
            "self": read(self_file),
            "self_meta": read_meta(self_meta),
            "context": result.stdout,
        }

    def test_self_line_moves_to_context_and_reply_list_keeps_order(self):
        lines = [
            "dociai: レトロゲームコーナーを開始します。",
            "alice: こんにちは",
            "bob: dociai: なりすまし本文",
            "DoCiAI: 私はDoCiAIです",
            "carol: 名前のない行です",
        ]
        entries = [
            _entry(0, lines[0], login="dociai", flags=["self"]),
            _entry(1, lines[1], login="alice"),
            _entry(2, lines[2], login="bob"),
            _entry(3, lines[3], login="mallory", display="DoCiAI"),
            # carol の行は metadata が欠落している (identity 不明)。
        ]
        out = self._run(lines, entries)
        self.assertEqual(out["self"], ["dociai: レトロゲームコーナーを開始します。"])
        # 返信対象は第三者コメントだけ。順序は元のまま。
        self.assertEqual(
            out["reply"],
            ["alice: こんにちは", "bob: dociai: なりすまし本文", "DoCiAI: 私はDoCiAIです", "carol: 名前のない行です"],
        )
        # 返信対象の metadata は行順のまま詰め直され、identity が保たれる。
        self.assertEqual([entry["line_index"] for entry in out["reply_meta"]], [0, 1, 2, 3])
        self.assertEqual([entry["login"] for entry in out["reply_meta"]], ["alice", "bob", "mallory", ""])
        self.assertEqual([entry["flags"] for entry in out["reply_meta"]], [[], [], [], []])
        self.assertEqual([entry["line_index"] for entry in out["self_meta"]], [0])
        self.assertEqual(out["self_meta"][0]["login"], "dociai")
        # 自分の投稿として提示される内容に、投稿者 identity と本文が入る。
        self.assertIn("dociai", out["context"])
        self.assertIn("dociai: レトロゲームコーナーを開始します。", out["context"])
        self.assertIn("返信対象ではありません", out["context"])
        self.assertNotIn("alice", out["context"])

    def test_all_self_batch_leaves_no_reply_target(self):
        lines = ["dociai: 改善ループを開始しました。", "dociai: 建国しました。"]
        entries = [_entry(0, lines[0], login="dociai", flags=["self"]), _entry(1, lines[1], login="dociai", flags=["self"])]
        out = self._run(lines, entries)
        self.assertEqual(out["reply"], [])
        self.assertEqual(out["reply_meta"], [])
        self.assertEqual(len(out["self"]), 2)
        self.assertIn("改善ループを開始しました。", out["context"])

    def test_missing_metadata_never_marks_a_line_as_self(self):
        lines = ["dociai: 本文は自己申告に見えるが identity が無い", "alice: こんにちは"]
        out = self._run(lines, None)
        self.assertEqual(out["reply"], lines)
        self.assertEqual(out["self"], [])
        self.assertEqual(out["context"], "（なし）")

    def test_trusted_card_self_line_keeps_both_flags(self):
        lines = ["aliceが【レア】赤いカードを獲得しました"]
        entries = [_entry(0, lines[0], login="dociai", flags=["self", "trusted-card"])]
        out = self._run(lines, entries)
        self.assertEqual(out["reply"], [])
        self.assertEqual(out["self"], lines)
        self.assertEqual(out["self_meta"][0]["flags"], ["self", "trusted-card"])

    def test_empty_self_batch_renders_none(self):
        lines = ["alice: こんにちは"]
        entries = [_entry(0, lines[0], login="alice")]
        out = self._run(lines, entries)
        self.assertEqual(out["self"], [])
        self.assertEqual(out["context"], "（なし）")


if __name__ == "__main__":
    unittest.main()
