"""YouTube pending-row acknowledgement identity (parity with twitch/kick).

The model-facing OUTFILE is NFKC-normalized by ``lib/comment_viewer_memory.py
emit-batch`` ("？！" -> "?!") while ``pending.log`` keeps the provider's original
full-width characters.  An exact ``$NF`` match therefore removed nothing, the row
stayed in ``pending.log`` forever, and once ``COMMENT_PROCESSED_LINES_TTL``
(1800s) expired the same comment was generated and spoken again - measured
2026-10-01: one YouTube comment answered 10 times between 09:34 and 14:18 UTC.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# A real YouTube live-chat id: '.' and '_' must stay acceptable.
MSG_TARGET = "LCC.EhwKGkNOMnNpZXU2bUpjREZYSEE1d01kajBZOUJB"
MSG_KEEP = "LCC.EhwKGkNOMnNpZXU2bUpjREZYSEE1d01kajBZOUJC"


def _env(chat_dir: Path, out: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "YOUTUBE_CHAT_DIR": str(chat_dir),
            "YOUTUBE_CHAT_OUTFILE": str(out),
            "CHAT_INGEST_OVERLAY_NOTIFY": "0",
        }
    )
    return env


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["bash", str(ROOT / "youtube_chat.sh"), *argv],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result


class YoutubeCommentIdentityTests(unittest.TestCase):
    def test_fetch_then_plain_ack_drains_the_full_width_row(self):
        """End-to-end reproduction of the production repeat loop."""
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            out = temp / "comments.txt"
            (chat_dir / "raw.log").write_text(
                f"id={MSG_TARGET}\tuser-id=UCgp4KyXL8FMudv1grbBpqvg\tlogin=UCgp4KyXL8FMudv1grbBpqvg\t"
                "display=@Empire.of.japan81\tflags=\t@Empire.of.japan81: ソ連配信？！\n",
                encoding="utf-8",
            )
            env = _env(chat_dir, out)

            fetched = _run(["fetch"], env)
            self.assertEqual(fetched.returncode, 0, fetched.stderr)
            # The model-facing line is normalized; pending keeps the original.
            self.assertEqual(out.read_text(encoding="utf-8"), "@Empire.of.japan81: ソ連配信?!\n")
            self.assertIn(
                "ソ連配信？！",
                (chat_dir / "pending.log").read_text(encoding="utf-8"),
            )

            batch = temp / "batch.txt"
            batch.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
            ack = _run(["ack-batch", str(batch)], env)
            self.assertEqual(ack.returncode, 0, ack.stderr)
            self.assertEqual((chat_dir / "pending.log").read_text(encoding="utf-8"), "")

    def test_ack_batch_uses_message_id_across_nfkc_text_changes(self):
        """Only the acknowledged viewer's row goes, even with identical text."""
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            out = temp / "comments.txt"
            (chat_dir / "raw.log").write_text(
                f"id={MSG_TARGET}\tuser-id=uid-1\tlogin=alice\tdisplay=Alice\tflags=\tAlice: わ～！（5回目）\n"
                f"id={MSG_KEEP}\tuser-id=uid-2\tlogin=bob\tdisplay=Bob\tflags=\tBob: わ～！（5回目）\n",
                encoding="utf-8",
            )
            env = _env(chat_dir, out)

            fetched = _run(["fetch"], env)
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
            self.assertEqual(batch.read_text(encoding="utf-8"), f"id={MSG_TARGET}\tAlice: わ~!(5回目)\n")

            ack = _run(["ack-batch", str(batch)], env)
            self.assertEqual(ack.returncode, 0, ack.stderr)
            pending = (chat_dir / "pending.log").read_text(encoding="utf-8")
            self.assertNotIn(MSG_TARGET, pending)
            self.assertIn(MSG_KEEP, pending)

    def test_legacy_plain_ack_normalizes_full_width_punctuation(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            (chat_dir / "pending.log").write_text(
                f"id={MSG_TARGET}\tuser-id=uid-1\tlogin=alice\tdisplay=Alice\tflags=\tAlice: わ～！（5回目）\n",
                encoding="utf-8",
            )
            batch = temp / "batch.txt"
            batch.write_text("Alice: わ~!(5回目)\n", encoding="utf-8")
            env = _env(chat_dir, temp / "comments.txt")

            ack = _run(["ack-batch", str(batch)], env)
            self.assertEqual(ack.returncode, 0, ack.stderr)
            self.assertEqual((chat_dir / "pending.log").read_text(encoding="utf-8"), "")

    def test_fetch_metadata_keeps_the_provider_message_id(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            chat_dir = temp / "chat"
            chat_dir.mkdir()
            out = temp / "comments.txt"
            (chat_dir / "raw.log").write_text(
                f"id={MSG_TARGET}\tuser-id=uid-1\tlogin=alice\tdisplay=Alice\tflags=\tAlice: こんにちは\n",
                encoding="utf-8",
            )
            env = _env(chat_dir, out)

            fetched = _run(["fetch"], env)
            self.assertEqual(fetched.returncode, 0, fetched.stderr)
            metadata = [
                json.loads(line)
                for line in Path(f"{out}.viewer_meta.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(metadata[0]["message_id"], MSG_TARGET)
            self.assertEqual(metadata[0]["source"], "youtube")


if __name__ == "__main__":
    unittest.main()
