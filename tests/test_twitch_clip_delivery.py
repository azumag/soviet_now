"""Clip HTTP/queue outcomes without contacting Twitch or sending chat."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClipSandbox(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="soren-clip-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        for name in ("bin", "lib", "tmp/clip_queue/done", "tmp/markers", "tmp/debug"):
            (self.work / name).mkdir(parents=True, exist_ok=True)
        self.env = {
            "PATH": str(self.work / "bin") + os.pathsep + os.environ["PATH"],
            "TWITCH_CLIENT_ID": "test-client",
            "TWITCH_BROADCASTER_ID": "1234",
            "TWITCH_CLIP_TOKEN": "test-token-do-not-log",
            "TWITCH_CLIP_ENABLED": "1",
            "TWITCH_CLIP_POLL_MAX": "2",
            "TWITCH_CLIP_POLL_INTERVAL_SEC": "0",
            "SOVIET_CELEBRATION_BLUESKY_ENABLED": "0",
        }

    def executable(self, path, source):
        target = self.work / path
        target.write_text(source)
        target.chmod(0o755)

    def run_shell(self, script):
        return subprocess.run(
            ["/bin/bash", "-c", script], cwd=self.work, env=self.env,
            text=True, capture_output=True, timeout=15,
        )


class TwitchClipHTTPTests(ClipSandbox):
    def setUp(self):
        super().setUp()
        shutil.copy(ROOT / "twitch_clip.sh", self.work / "twitch_clip.sh")
        (self.work / "lib/outbound_queue.sh").write_text(
            'enqueue_chat_message() { printf "%s\\n" "$1" >> chat.txt; }\n'
        )
        self.executable("bin/curl", """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
post = '-X' in args
assert '--connect-timeout' in args and '--max-time' in args
with open('http_calls.txt', 'a') as f:
    f.write(('POST' if post else 'GET') + '\\n')
mode = os.environ.get('HTTP_TEST_MODE', 'success')
if post:
    if mode == 'timeout':
        print('000')
        sys.exit(28)
    if mode == 'dns':
        print('000')
        sys.exit(6)
    code = {'unavailable':'503', 'unauthorized':'401', 'rate_limit':'429'}.get(mode, '202')
    print(json.dumps({'data':[{'id':'TestClip'}]}))
    print(code)
elif mode == 'unconfirmed':
    print('{"data":[]}')
else:
    print(json.dumps({'data':[{'url':'https://clips.twitch.tv/TestClip'}]}))
""")

    def run_clip(self, mode="success"):
        self.env["HTTP_TEST_MODE"] = mode
        result = self.run_shell("bash ./twitch_clip.sh celebration soviet")
        self.assertNotIn("test-token-do-not-log", result.stdout + result.stderr)
        return result

    def test_confirmed_clip_is_enqueued_once(self):
        result = self.run_clip()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / "chat.txt").read_text(),
                         "celebration | https://clips.twitch.tv/TestClip\n")
        self.assertEqual((self.work / "http_calls.txt").read_text(), "POST\nGET\n")

    def test_missing_credentials_is_not_success(self):
        self.env.pop("TWITCH_CLIP_TOKEN")
        result = self.run_clip()
        self.assertEqual(result.returncode, 78)
        self.assertFalse((self.work / "http_calls.txt").exists())

    def test_explicit_rejections_and_connection_failure_are_retryable(self):
        for mode in ("unavailable", "rate_limit", "dns"):
            with self.subTest(mode=mode):
                result = self.run_clip(mode)
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertFalse((self.work / "chat.txt").exists())

    def test_authentication_rejection_is_not_success_or_retryable(self):
        self.assertEqual(self.run_clip("unauthorized").returncode, 78)

    def test_ambiguous_timeout_is_not_automatically_retryable(self):
        self.assertEqual(self.run_clip("timeout").returncode, 1)
        self.assertEqual((self.work / "http_calls.txt").read_text(), "POST\n")

    def test_unconfirmed_clip_never_posts_a_dead_link(self):
        result = self.run_clip("unconfirmed")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.work / "http_calls.txt").read_text(), "POST\nGET\nGET\n")
        self.assertFalse((self.work / "chat.txt").exists())


class ClipQueueTests(ClipSandbox):
    def setUp(self):
        super().setUp()
        worker = (ROOT / "workers/chat_worker.sh").read_text()
        process = re.search(r"(?ms)^_process_clip_queue\(\) \{.*?^\}", worker).group()
        (self.work / "queue_function.sh").write_text(process)
        self.executable("twitch_clip.sh", """#!/usr/bin/env python3
import os, sys
from pathlib import Path
calls = Path('clip_calls.txt')
count = len(calls.read_text().splitlines()) if calls.exists() else 0
with calls.open('a') as f:
    f.write(sys.argv[2] + '\\n')
codes = os.environ.get('CLIP_TEST_RCS', '0').split(',')
sys.exit(int(codes[min(count, len(codes) - 1)]))
""")
        self.preamble = """
CLIP_QUEUE_DIR=tmp/clip_queue
CLIP_QUEUE_DONE_DIR=tmp/clip_queue/done
TMP_MARKERS_DIR=tmp/markers
TMP_DEBUG_DIR=tmp/debug
_log() { echo "$*"; }
source ./queue_function.sh
"""

    def enqueue(self, name, kind="soviet", **extras):
        event = dict(event_msg="celebration", game_id="42", delay=0, event_kind=kind)
        event.update(extras)
        (self.work / f"tmp/clip_queue/{name}.json").write_text(json.dumps(event))

    def process(self, ticks=1):
        result = self.run_shell(self.preamble + "\n_process_clip_queue\n" * ticks)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def calls(self):
        path = self.work / "clip_calls.txt"
        return path.read_text().splitlines() if path.exists() else []

    def test_general_clip_does_not_suppress_later_soviet(self):
        self.enqueue("001", "generic")
        self.enqueue("002", "soviet")
        self.enqueue("003", "soviet")
        self.process()
        self.assertEqual(self.calls(), ["generic", "soviet"])
        self.assertEqual(len(list((self.work / "tmp/clip_queue/done").glob("*.json"))), 3)

    def test_soviet_clip_still_suppresses_later_high_score(self):
        self.enqueue("001", "soviet")
        self.enqueue("002", "generic")
        self.process()
        self.assertEqual(self.calls(), ["soviet"])

    def test_retryable_failure_keeps_event_then_succeeds(self):
        self.env["CLIP_TEST_RCS"] = "75,0"
        self.enqueue("001")
        self.process()
        pending = self.work / "tmp/clip_queue/001.json"
        self.assertEqual(json.loads(pending.read_text())["attempts"], 1)
        self.assertFalse((self.work / "tmp/markers/.twitch_clip_game_42_soviet").exists())
        self.assertFalse((self.work / "tmp/clip_queue/done/001.json").exists())
        self.process()
        self.assertEqual(self.calls(), ["soviet", "soviet"])
        self.assertTrue((self.work / "tmp/clip_queue/done/001.json").exists())

    def test_retries_are_bounded_and_failure_is_not_done(self):
        self.env["CLIP_TEST_RCS"] = "75"
        self.enqueue("001")
        self.process(ticks=5)
        self.assertEqual(len(self.calls()), 3)
        self.assertTrue((self.work / "tmp/clip_queue/failed/001.json").exists())
        self.assertFalse((self.work / "tmp/clip_queue/done/001.json").exists())
        self.assertEqual((self.work / "tmp/markers/.twitch_clip_game_42_soviet/failed_rc").read_text(), "75\n")

    def test_ambiguous_failure_is_preserved_without_duplicate_post(self):
        self.env["CLIP_TEST_RCS"] = "1"
        self.enqueue("001")
        self.enqueue("002")
        self.process(ticks=2)
        self.assertEqual(self.calls(), ["soviet"])
        self.assertEqual(len(list((self.work / "tmp/clip_queue/failed").glob("*.json"))), 2)
        self.assertEqual(list((self.work / "tmp/clip_queue/done").glob("*.json")), [])

    def test_invalid_json_cannot_reuse_preceding_event(self):
        self.enqueue("001")
        (self.work / "tmp/clip_queue/002.json").write_text("{")
        self.process()
        self.assertEqual(self.calls(), ["soviet"])
        self.assertTrue((self.work / "tmp/clip_queue/failed/002.json").exists())

    def test_stop_before_request_releases_claim_and_retains_event(self):
        self.enqueue("001", delay=1)
        (self.work / "tmp/stop").touch()
        self.process()
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.work / "tmp/clip_queue/001.json").exists())
        self.assertFalse((self.work / "tmp/markers/.twitch_clip_game_42_soviet").exists())


class ClipEnqueueTests(ClipSandbox):
    def test_soviet_escapes_and_survives_same_tick_general_event(self):
        shutil.copy(ROOT / "core/version.sh", self.work / "version.sh")
        result = self.run_shell("""
log() { :; }
source ./version.sh
_create_twitch_clip generic 42 0
_create_twitch_clip 'quote " and backslash \\ and newline
celebration' 42 0 soviet
_create_twitch_clip duplicate 42 0 soviet
_create_twitch_clip highscore 42 0
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(p.read_text()) for p in (self.work / "tmp/clip_queue").glob("*.json")]
        self.assertEqual(len(events), 2)
        self.assertEqual({e["event_kind"] for e in events}, {"generic", "soviet"})
        soviet = next(e for e in events if e["event_kind"] == "soviet")
        self.assertEqual(soviet["event_msg"], 'quote " and backslash \\ and newline\ncelebration')


if __name__ == "__main__":
    unittest.main()
