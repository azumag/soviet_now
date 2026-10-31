import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "lib" / "outbound_queue_order.py"
SPEC = importlib.util.spec_from_file_location("outbound_queue_order", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class OutboundQueueOrderingTests(unittest.TestCase):
    def test_orders_by_priority_then_enqueue_time_then_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp)
            for name in (
                "300_low_9.msg",
                "200_high_b_1.msg",
                "100_mid_3.msg",
                "200_high_a_1.msg",
            ):
                (pending / name).write_text(name, encoding="utf-8")

            ordered = sorted(pending.glob("*.msg"), key=MODULE.pending_sort_key)
            self.assertEqual(
                [path.name for path in ordered],
                [
                    "200_high_a_1.msg",
                    "200_high_b_1.msg",
                    "100_mid_3.msg",
                    "300_low_9.msg",
                ],
            )

    def test_source_with_underscores_does_not_change_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "123_comment_worker_reply_2.msg"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(MODULE.pending_sort_key(path), (2, 123, path.name))

    def test_legacy_or_broken_priority_defaults_to_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp)
            legacy = pending / "100_legacy-source.msg"
            broken = pending / "101_source_broken.msg"
            current = pending / "102_source_7.msg"
            for path in (legacy, broken, current):
                path.write_text("hello", encoding="utf-8")

            self.assertEqual(MODULE.pending_sort_key(legacy)[0], 5)
            self.assertEqual(MODULE.pending_sort_key(broken)[0], 5)
            self.assertEqual(MODULE.pending_sort_key(current)[0], 7)
            self.assertEqual(MODULE.select_next_pending(pending), legacy)

    def test_selection_is_stable_for_same_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp)
            for name in (
                "500_source_z_2.msg",
                "500_source_a_2.msg",
                "400_source_4.msg",
            ):
                (pending / name).write_text("hello", encoding="utf-8")

            picks = [MODULE.select_next_pending(pending).name for _ in range(20)]
            self.assertEqual(picks, ["500_source_a_2.msg"] * 20)

    def test_shell_consumer_uses_priority_order_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue"
            twitch_log = root / "twitch.log"
            fake_twitch = root / "twitch_chat.sh"
            fake_twitch.write_text(
                "#!/usr/bin/env bash\n"
                "set -eu\n"
                "[ \"${1:-}\" = send ] || exit 2\n"
                "printf '%s\\n' \"${2:-}\" >> \"$TWITCH_TEST_LOG\"\n",
                encoding="utf-8",
            )
            fake_twitch.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "OUTBOUND_CHAT_QUEUE_DIR": str(queue),
                    "OUTBOUND_CHAT_ENQUEUE_DEDUP_TTL_SEC": "0",
                    "TWITCH_TEST_LOG": str(twitch_log),
                    "OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED": "0",
                    "YOUTUBE_CHAT_SEND_ENABLED": "0",
                }
            )
            script = f"""
set -eu
cd {str(root)!r}
source {str(REPO_ROOT / 'lib' / 'outbound_queue.sh')!r}
enqueue_chat_message 'low' 'source_with_under_score' 9
enqueue_chat_message 'high' 'comment_worker_reply' 1
enqueue_chat_message 'middle' 'radio' 5
outbound_queue_consume_once
basename=$(find \"$OUTBOUND_CHAT_SENT_DIR\" -type f -name '*.msg' -print -quit)
_outbound_chat_source_from_basename \"$(basename \"$basename\")\"
"""
            result = subprocess.run(
                ["bash", "-lc", script],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(twitch_log.read_text(encoding="utf-8").splitlines(), ["high"])
            self.assertEqual(result.stdout.strip(), "comment_worker_reply")


if __name__ == "__main__":
    unittest.main()
