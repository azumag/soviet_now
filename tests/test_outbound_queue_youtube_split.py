from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestOutboundQueueYoutubeMirrorSplit(unittest.TestCase):
    def test_youtube_mirror_splits_without_shortening_twitch_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "lib").mkdir()
            shutil.copy2(ROOT / "lib" / "outbound_queue.sh", work / "lib" / "outbound_queue.sh")

            for name, log_var in (("twitch_chat.sh", "TWITCH_LOG"), ("youtube_chat.sh", "YOUTUBE_LOG")):
                script = work / name
                script.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -eu\n"
                    "[ \"$1\" = send ]\n"
                    f"printf '%s\\n' \"$2\" >> \"${log_var}\"\n",
                    encoding="utf-8",
                )
                script.chmod(0o755)

            message = (
                "アルマムーン城への進出を狙い、アンディーブ将軍を出撃させました。"
                "ジョンリギ城ではバジルとの戦闘に勝っています。"
                "その一方でナキューメラ城の失陥が確認されました。"
                "次回は出撃前後の守備配置と兵力を確認します。"
            )
            self.assertGreater(len(message.encode("utf-8")), 200)
            self.assertLessEqual(len(message.encode("utf-8")), 430)

            env = os.environ.copy()
            env.update(
                OUTBOUND_CHAT_QUEUE_DIR=str(work / "queue"),
                OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED="1",
                YOUTUBE_OAUTH_CLIENT_ID="test-client",
                YOUTUBE_OAUTH_CLIENT_SECRET="test-secret",
                YOUTUBE_OAUTH_REFRESH_TOKEN="test-refresh",
                TWITCH_LOG=str(work / "twitch.log"),
                YOUTUBE_LOG=str(work / "youtube.log"),
            )
            subprocess.run(
                [
                    "bash",
                    "-c",
                    'source lib/outbound_queue.sh; '
                    'enqueue_chat_message "$1" "retro-corner" 5; '
                    "outbound_queue_consume_once",
                    "bash",
                    message,
                ],
                cwd=work,
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )

            twitch_parts = (work / "twitch.log").read_text(encoding="utf-8").splitlines()
            youtube_parts = (work / "youtube.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(twitch_parts, [message])
            self.assertGreater(len(youtube_parts), 1)
            self.assertTrue(all(len(part.encode("utf-8")) <= 200 for part in youtube_parts))
            self.assertEqual("".join(youtube_parts), message)
            self.assertTrue(all(part[-1] in "。！？!?" for part in youtube_parts[:-1]))


if __name__ == "__main__":
    unittest.main()
