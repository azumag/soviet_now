"""Exercise the actual Bash queue/splitter with isolated, synthetic senders."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MESSAGE = (
    "アルマムーン城への進出を狙い、アンディーブ将軍を出撃させました。"
    "ジョンリギ城ではバジルとの戦闘に勝っています。"
    "その一方でナキューメラ城の失陥が確認されました。"
    "次回は出撃前後の守備配置と兵力を確認します。"
)


class TestOutboundQueueYoutubeMirrorSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        (self.work / "lib").mkdir()
        shutil.copy2(ROOT / "lib" / "outbound_queue.sh", self.work / "lib" / "outbound_queue.sh")
        # Never source the checkout's .env or invoke a real provider script.
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith(("OUTBOUND_CHAT_", "YOUTUBE_", "TMP_DEBUG_")):
                self.env.pop(name)
        self.env.update(
            OUTBOUND_CHAT_QUEUE_DIR=str(self.work / "queue with spaces"),
            OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED="1",
            YOUTUBE_OAUTH_CLIENT_ID="synthetic-client",
            YOUTUBE_OAUTH_CLIENT_SECRET="synthetic-secret",
            YOUTUBE_OAUTH_REFRESH_TOKEN="synthetic-refresh",
            TMP_DEBUG_DIR=str(self.work / "debug"),
            TWITCH_LOG=str(self.work / "twitch.log"),
            YOUTUBE_LOG=str(self.work / "youtube.log"),
            YOUTUBE_ATTEMPT_LOG=str(self.work / "youtube-attempts.log"),
            TWITCH_FAIL="0",
            YOUTUBE_FAIL_AT="0",
        )
        self.write_script(
            "twitch_chat.sh",
            '[ "$1" = send ]\n'
            'if [ "$TWITCH_FAIL" = 1 ]; then echo "synthetic twitch failure" >&2; exit 42; fi\n'
            'printf "%s\\n" "$2" >> "$TWITCH_LOG"\n',
        )
        self.write_script(
            "youtube_chat.sh",
            '[ "$1" = send ]\n'
            'printf "%s\\n" "$2" >> "$YOUTUBE_ATTEMPT_LOG"\n'
            'count=$(wc -l < "$YOUTUBE_ATTEMPT_LOG")\n'
            'if [ "$count" -eq "$YOUTUBE_FAIL_AT" ]; then echo "synthetic youtube failure" >&2; exit 42; fi\n'
            'printf "%s\\n" "$2" >> "$YOUTUBE_LOG"\n',
        )

    def write_script(self, name, body):
        script = self.work / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env bash\nset -eu\n" + body, encoding="utf-8")
        script.chmod(0o755)

    def shell(self, command, *args):
        return subprocess.run(
            ["bash", "-c", 'source lib/outbound_queue.sh; ' + command, "test", *args],
            cwd=self.work, env=self.env, text=True, encoding="utf-8",
            capture_output=True, timeout=10,
        )

    def consume(self, message=MESSAGE, source="retro-corner"):
        return self.shell('enqueue_chat_message "$1" "$2" 5 && outbound_queue_consume_once', message, source)

    def split(self, message, limit=None):
        args = [message] if limit is None else [message, str(limit)]
        command = '_outbound_chat_split_utf8 "$1"' if limit is None else '_outbound_chat_split_utf8 "$1" "$2"'
        return self.shell(command, *args)

    def lines(self, name):
        path = self.work / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def assert_complete_split(self, result, message, limit=200):
        self.assertEqual(result.returncode, 0, result.stderr)
        parts = result.stdout.splitlines()
        self.assertEqual("".join(parts), message)
        self.assertTrue(all(0 < len(part.encode("utf-8")) <= limit for part in parts))
        return parts

    def assert_sent_once(self, message=MESSAGE):
        self.assertEqual(self.lines("twitch.log"), [message])
        queue = Path(self.env["OUTBOUND_CHAT_QUEUE_DIR"])
        self.assertEqual(len(list((queue / "sent").glob("*.msg"))), 1)
        self.assertFalse(list((queue / "pending").glob("*.msg")))
        self.assertFalse(list((queue / "processing").glob("*.msg")))
        self.assertFalse(list(queue.glob(".youtube_*")))

    def test_youtube_mirror_splits_without_shortening_twitch_message(self):
        self.assertGreater(len(MESSAGE.encode("utf-8")), 200)
        self.assertLessEqual(len(MESSAGE.encode("utf-8")), 430)
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        parts = self.lines("youtube.log")
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), MESSAGE)
        self.assertTrue(all(len(part.encode("utf-8")) <= 200 for part in parts))
        self.assertTrue(all(part[-1] in "。！？!?" for part in parts[:-1]))
        self.assertEqual(self.lines("youtube-attempts.log"), parts)

    def test_default_limit_and_exact_ascii_boundary(self):
        for count in (1, 199, 200, 201, 400, 401):
            with self.subTest(count=count):
                parts = self.assert_complete_split(self.split("x" * count), "x" * count)
                self.assertEqual(len(parts), (count + 199) // 200)

    def test_utf8_fallback_without_punctuation_preserves_every_character(self):
        for message in ("あ" * 150, "🦊" * 101, "abc漢字🦊" * 70):
            with self.subTest(message=message[:8]):
                parts = self.assert_complete_split(self.split(message), message)
                self.assertGreater(len(parts), 1)

    def test_clause_and_space_fallbacks_preserve_order(self):
        for separator in ("、", "，", ",", " ", "\t"):
            with self.subTest(separator=separator):
                message = "あ" * 40 + separator + "い" * 40
                parts = self.assert_complete_split(self.split(message), message)
                self.assertEqual(parts[0], "あ" * 40 + separator)

    def test_early_punctuation_does_not_create_tiny_fragment(self):
        message = "短文。" + "あ" * 100
        parts = self.assert_complete_split(self.split(message), message)
        self.assertGreaterEqual(len(parts[0].encode("utf-8")), 100)

    def test_custom_limit_reaches_both_splitter_and_mirror(self):
        parts = self.assert_complete_split(self.split(MESSAGE, 90), MESSAGE, 90)
        self.env["OUTBOUND_CHAT_YOUTUBE_MAX_BYTES"] = "90"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertEqual(self.lines("youtube.log"), parts)

    def test_limit_override_cannot_exceed_the_youtube_sink_budget(self):
        parts = self.assert_complete_split(self.split(MESSAGE, 300), MESSAGE)
        self.env["OUTBOUND_CHAT_YOUTUBE_MAX_BYTES"] = "300"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertEqual(self.lines("youtube.log"), parts)

    def test_newlines_are_normalized_and_shell_syntax_stays_literal(self):
        message = "前半\r\n$(touch escaped-marker); ${HOME} `uname` \\ 後半" * 10
        expected = message.replace("\r", " ").replace("\n", " ")
        self.assert_complete_split(self.split(message), expected)
        self.assertFalse((self.work / "escaped-marker").exists())

    def test_empty_message_emits_nothing(self):
        result = self.split("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_and_too_small_limits_fail(self):
        for limit in ("0", "-1", "abc", "1.5", "1", "2"):
            with self.subTest(limit=limit):
                result = self.split("あ", limit)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_mktemp_failure_uses_expanded_queue_path_and_cleans_up(self):
        self.write_script("bin/mktemp", "exit 1\n")
        self.env["PATH"] = str(self.work / "bin") + os.pathsep + self.env["PATH"]
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertEqual("".join(self.lines("youtube.log")), MESSAGE)
        self.assertFalse(result.stderr)

    def test_split_failure_is_logged_without_rolling_back_twitch(self):
        self.env["OUTBOUND_CHAT_YOUTUBE_MAX_BYTES"] = "invalid"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertFalse(self.lines("youtube-attempts.log"))
        self.assertIn("youtube mirror split failed", "\n".join(self.lines("debug/outbound_chat_youtube.log")))

    def test_python_failure_sends_no_partial_mirror_and_keeps_twitch_sent(self):
        # A failed splitter may emit data before failing; no partial output is sent.
        self.write_script("bin/python3", "printf 'partial splitter output\\n'; exit 42\n")
        self.env["PATH"] = str(self.work / "bin") + os.pathsep + self.env["PATH"]
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertFalse(self.lines("youtube-attempts.log"))
        self.assertIn("youtube mirror split failed", "\n".join(self.lines("debug/outbound_chat_youtube.log")))

    def test_youtube_send_failure_stops_at_failed_part_without_retrying_twitch(self):
        parts = self.assert_complete_split(self.split(MESSAGE), MESSAGE)
        self.env["YOUTUBE_FAIL_AT"] = "2"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertEqual(self.lines("youtube.log"), parts[:1])
        self.assertEqual(self.lines("youtube-attempts.log"), parts[:2])
        self.assertIn("synthetic youtube failure", "\n".join(self.lines("debug/outbound_chat_youtube.log")))
        self.assertEqual(self.shell("outbound_queue_consume_once").returncode, 1)
        self.assertEqual(self.lines("twitch.log"), [MESSAGE])

    def test_twitch_failure_returns_nonzero_and_restores_pending_without_mirror(self):
        self.env["TWITCH_FAIL"] = "1"
        result = self.consume()
        self.assertEqual(result.returncode, 1, result.stderr)
        queue = Path(self.env["OUTBOUND_CHAT_QUEUE_DIR"])
        self.assertEqual(len(list((queue / "pending").glob("*.msg"))), 1)
        self.assertFalse(list((queue / "sent").glob("*.msg")))
        self.assertFalse(list((queue / "processing").glob("*.msg")))
        self.assertFalse(self.lines("youtube-attempts.log"))
        self.assertIn("synthetic twitch failure", "\n".join(self.lines("debug/outbound_chat_twitch.log")))

    def test_disabled_mirror_keeps_twitch_only(self):
        self.env["OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED"] = "0"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertFalse(self.lines("youtube-attempts.log"))

    def test_excluded_source_keeps_twitch_only(self):
        self.env["OUTBOUND_CHAT_YOUTUBE_MIRROR_EXCLUDE_SOURCES"] = "other retro-corner"
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertFalse(self.lines("youtube-attempts.log"))

    def test_youtube_quota_backoff_keeps_twitch_only(self):
        chat_dir = self.work / "youtube"
        chat_dir.mkdir()
        (chat_dir / "api_backoff_until").write_text("9999999999\n", encoding="utf-8")
        self.env["YOUTUBE_CHAT_DIR"] = str(chat_dir)
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sent_once()
        self.assertFalse(self.lines("youtube-attempts.log"))


if __name__ == "__main__":
    unittest.main()
