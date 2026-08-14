from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TwitchChatDaemonTests(unittest.TestCase):
    def _extract_usernotice_message(self, payload: str) -> str:
        with tempfile.TemporaryDirectory() as chat_dir:
            env = os.environ.copy()
            env["TWITCH_CHAT_DIR"] = chat_dir
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    "source <(sed '/^while true; do/,$d' twitch_chat_daemon.sh); "
                    "_extract_usernotice_message \"$1\"",
                    "bash",
                    payload,
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        return result.stdout.rstrip("\n")

    def test_usernotice_without_message_is_empty(self):
        payload = ":tmi.twitch.tv USERNOTICE #dociai\r"

        self.assertEqual(self._extract_usernotice_message(payload), "")

    def test_usernotice_with_message_returns_only_message(self):
        payload = ":tmi.twitch.tv USERNOTICE #dociai :配信いつもありがとう！\r"

        self.assertEqual(
            self._extract_usernotice_message(payload),
            "配信いつもありがとう！",
        )


if __name__ == "__main__":
    unittest.main()
