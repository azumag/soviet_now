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

    def test_daemon_has_irc_read_timeout_wiring(self):
        # Regression guard for the CLOSE-WAIT hang: the IRC read loop must run
        # under a bounded read timeout and log a stall when it is exceeded.
        src = (REPO_ROOT / "twitch_chat_daemon.sh").read_text(encoding="utf-8")
        self.assertIn("TWITCH_IRC_READ_TIMEOUT_SEC:-600", src)
        self.assertIn('read -r -t "$IRC_READ_TIMEOUT_SEC"', src)
        self.assertIn("-gt 128", src)
        self.assertIn("IRC heartbeat stall", src)

    def test_irc_read_timeout_detects_stalled_connection(self):
        # Reproduce the daemon's exact inner-read pattern (while : + if read -t
        # + capture rc in the else branch) against a silent writer, which
        # mirrors a CLOSE-WAIT / half-open hang: the read must time out and
        # capture rc>128. An active writer must not be misdetected as a stall.
        with tempfile.TemporaryDirectory() as chat_dir:
            env = os.environ.copy()
            env["TWITCH_CHAT_DIR"] = chat_dir
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
set -e
source <(sed '/^while true; do/,$d' twitch_chat_daemon.sh) 2>/dev/null || true
IRC_READ_TIMEOUT_SEC=1

# silent coproc → read -t must exceed the ceiling and capture rc>128,
# mirroring the daemon's while : / if read -t / else / break structure
coproc SILENT { sleep 5; }
_irc_read_rc=0
while :; do
    if IFS= read -r -t "$IRC_READ_TIMEOUT_SEC" line <&"${SILENT[0]}"; then
        :
    else
        _irc_read_rc=$?
        break
    fi
done
exec {SILENT[0]}>&- 2>/dev/null || true
exec {SILENT[1]}>&- 2>/dev/null || true
wait "$SILENT_PID" 2>/dev/null || true
[ "$_irc_read_rc" -gt 128 ]

# active coproc → data arrives immediately, no spurious stall
coproc TALK { printf 'PING tmi.twitch.tv\r\n'; sleep 3; }
got=""
while :; do
    if IFS= read -r -t "$IRC_READ_TIMEOUT_SEC" line <&"${TALK[0]}"; then
        :
    else
        break
    fi
    got="$line"
    break
done
exec {TALK[0]}>&- 2>/dev/null || true
exec {TALK[1]}>&- 2>/dev/null || true
wait "$TALK_PID" 2>/dev/null || true
[ "${got%$'\r'}" = "PING tmi.twitch.tv" ]
''',
                    "bash",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
