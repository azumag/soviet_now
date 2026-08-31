import sys
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.twitch_goal_monitor import process


class TwitchGoalMonitorTest(unittest.TestCase):
    def goal(self, current=9, target=10, goal_id="g1", goal_type="follower"):
        return {"data": [{"id": goal_id, "type": goal_type, "description": "同志100人", "current_amount": current, "target_amount": target, "created_at": "2026-09-01T00:00:00Z"}]}

    def test_first_observation_is_baseline_even_if_complete(self):
        state, events = process(self.goal(current=10), {})
        self.assertEqual([], events)
        self.assertTrue(state["goals"]["g1"]["complete"])

    def test_emits_only_incomplete_to_complete_crossing(self):
        state, events = process(self.goal(current=9), {})
        self.assertEqual([], events)
        state, events = process(self.goal(current=10), state)
        self.assertEqual(1, len(events))
        self.assertEqual("follower", events[0]["type"])
        state, events = process(self.goal(current=11), state)
        self.assertEqual([], events)
        state, events = process(self.goal(current=9), state)
        self.assertEqual([], events)
        state, events = process(self.goal(current=10), state)
        self.assertEqual([], events)

    def test_supports_multiple_goal_types(self):
        baseline = {"version": 1, "goals": {"sub": {"complete": False, "celebrated": False}}}
        payload = self.goal(current=5, target=5, goal_id="sub", goal_type="subscription_count")
        _, events = process(payload, baseline)
        self.assertEqual("subscription_count", events[0]["type"])

    def test_worker_queues_one_synthetic_comment_on_crossing(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fake_bin = tmp / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/bin/bash
out=''
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift 2; else shift; fi
done
cp "$GOAL_PAYLOAD" "$out"
printf '200'
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            payload = tmp / "payload.json"
            state = tmp / "state.json"
            chat_dir = tmp / "chat"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "GOAL_PAYLOAD": str(payload),
                    "TWITCH_GOALS_ENABLED": "1",
                    "TWITCH_GOALS_TOKEN": "test-token",
                    "TWITCH_CLIENT_ID": "test-client",
                    "TWITCH_BROADCASTER_ID": "test-broadcaster",
                    "TWITCH_GOALS_STATE_FILE": str(state),
                    "TWITCH_CHAT_DIR": str(chat_dir),
                }
            )

            payload.write_text(json.dumps(self.goal(current=9)), encoding="utf-8")
            subprocess.run(["bash", "workers/goal_worker.sh", "--once"], cwd=root, env=env, check=True)
            self.assertFalse((chat_dir / "raw.log").exists())

            payload.write_text(json.dumps(self.goal(current=10)), encoding="utf-8")
            subprocess.run(["bash", "workers/goal_worker.sh", "--once"], cwd=root, env=env, check=True)
            lines = (chat_dir / "raw.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            self.assertIn("[配信目標達成] フォロワー目標", lines[0])
            self.assertIn("10/10", lines[0])

            subprocess.run(["bash", "workers/goal_worker.sh", "--once"], cwd=root, env=env, check=True)
            self.assertEqual(lines, (chat_dir / "raw.log").read_text(encoding="utf-8").splitlines())

    def test_goal_event_is_wired_into_ai_comment_path(self):
        root = Path(__file__).resolve().parents[1]
        classifier = (root / "prompts/comment_classifier.md").read_text(encoding="utf-8")
        response = (root / "prompts/comment_response_default.md").read_text(encoding="utf-8")
        comment = (root / "broadcast/comment.sh").read_text(encoding="utf-8")
        supervisor = (root / "start_all.sh").read_text(encoding="utf-8")
        self.assertIn("stream_goal", classifier)
        self.assertIn("[配信目標達成]", response)
        self.assertIn('return "stream_goal"', comment)
        self.assertIn("./workers/goal_worker.sh", supervisor)
        self.assertIn('goal_worker) echo "tmp/state/goal_worker.pid"', supervisor)


if __name__ == "__main__":
    unittest.main()
