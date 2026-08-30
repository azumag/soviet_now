from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TwitchCommentIdentityTests(unittest.TestCase):
    def test_fetch_preserves_message_scoped_stable_identity(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            out = temp / "comments.txt"
            (chat_dir / "last_offset").write_text("0\n", encoding="utf-8")
            (chat_dir / "raw.log").write_text(
                "id=msg-1\tuser-id=uid-1\tlogin=alice-login\tdisplay=Alice: QA\tflags=\tAlice: QA: hello\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "TWITCH_CHAT_DIR": str(chat_dir),
                    "TWITCH_CHAT_OUTFILE": str(out),
                    "CHAT_INGEST_OVERLAY_NOTIFY": "0",
                }
            )

            result = subprocess.run(
                ["bash", str(ROOT / "twitch_chat.sh"), "fetch"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), "Alice: QA: hello\n")
            metadata = [
                json.loads(line)
                for line in Path(f"{out}.viewer_meta.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(metadata[0]["message_id"], "msg-1")
            self.assertEqual(metadata[0]["stable_id"], "uid-1")
            self.assertEqual(metadata[0]["display_name"], "Alice: QA")

            batch = temp / "batch.txt"
            batch.write_text("Alice: QA: hello\n", encoding="utf-8")
            ack = subprocess.run(
                ["bash", str(ROOT / "twitch_chat.sh"), "ack-batch", str(batch)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(ack.returncode, 0, ack.stderr)
            self.assertEqual((chat_dir / "pending.log").read_text(encoding="utf-8"), "")

    def test_ack_batch_uses_message_id_across_nfkc_text_changes(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            out = temp / "comments.txt"
            (chat_dir / "last_offset").write_text("0\n", encoding="utf-8")
            (chat_dir / "raw.log").write_text(
                "id=msg-target\tuser-id=uid-1\tlogin=alice\tdisplay=Alice\tflags=\tAlice: わ～！（5回目）\n"
                "id=msg-keep\tuser-id=uid-2\tlogin=bob\tdisplay=Bob\tflags=\tBob: わ～！（5回目）\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "TWITCH_CHAT_DIR": str(chat_dir),
                    "TWITCH_CHAT_OUTFILE": str(out),
                    "CHAT_INGEST_OVERLAY_NOTIFY": "0",
                }
            )

            fetched = subprocess.run(
                ["bash", str(ROOT / "twitch_chat.sh"), "fetch"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(fetched.returncode, 0, fetched.stderr)
            self.assertIn("Alice: わ~!(5回目)", out.read_text(encoding="utf-8"))

            batch = temp / "batch.txt"
            batch.write_text("Alice: わ~!(5回目)\n", encoding="utf-8")
            emitted = subprocess.run(
                [
                    "python3",
                    str(ROOT / "lib" / "comment_viewer_memory.py"),
                    "emit-ack-batch",
                    "--metadata",
                    f"{out}.viewer_meta.jsonl",
                    "--batch",
                    str(batch),
                    "--out",
                    str(batch),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(emitted.returncode, 0, emitted.stderr)
            self.assertEqual(batch.read_text(encoding="utf-8"), "id=msg-target\tAlice: わ~!(5回目)\n")

            ack = subprocess.run(
                ["bash", str(ROOT / "twitch_chat.sh"), "ack-batch", str(batch)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(ack.returncode, 0, ack.stderr)
            pending = (chat_dir / "pending.log").read_text(encoding="utf-8")
            self.assertNotIn("id=msg-target", pending)
            self.assertIn("id=msg-keep", pending)

    def test_legacy_plain_ack_normalizes_full_width_punctuation(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            (chat_dir / "pending.log").write_text(
                "id=msg-1\tuser-id=uid-1\tlogin=alice\tdisplay=Alice\tflags=\tAlice: わ～！（5回目）\n",
                encoding="utf-8",
            )
            batch = temp / "batch.txt"
            batch.write_text("Alice: わ~!(5回目)\n", encoding="utf-8")
            env = os.environ.copy()
            env["TWITCH_CHAT_DIR"] = str(chat_dir)

            ack = subprocess.run(
                ["bash", str(ROOT / "twitch_chat.sh"), "ack-batch", str(batch)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(ack.returncode, 0, ack.stderr)
            self.assertEqual((chat_dir / "pending.log").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
