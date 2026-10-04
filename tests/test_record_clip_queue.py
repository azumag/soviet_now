"""Real local queue/script/receipt paths with a synthetic HTTP executable."""
import json
from pathlib import Path
import shutil
import time

from tests import test_twitch_clip_delivery as http_fixture
ROOT = http_fixture.ROOT


class RecordClipTests(http_fixture.TwitchClipHTTPFixture):
    def setUp(self):
        super().setUp()
        (self.work / "tools").mkdir()
        for name in ("clip_receipt.py", "record_clip_queue.py"):
            shutil.copy(ROOT / "tools" / name, self.work / "tools" / name)
        self.key = "a" * 64
        self.event_path = self.work / "tmp/clip_queue" / ("record_" + self.key + ".json")
        self.receipt_path = self.work / "tmp/clip_queue/receipts" / (self.key + ".json")
        self.event_path.write_text(json.dumps({
            "schema": 1, "event_id": self.key, "event_kind": "record",
            "event_msg": "🏆 Bastet 新記録: score=12（前記録 11）",
            "created_at": time.time(), "delay": 0, "game_id": "",
            "record": {"game": "bastet", "metric": "score", "value": 12, "previous": 11},
        }))

    def process_record(self, mode="success"):
        self.env["HTTP_TEST_MODE"] = mode
        result = self.run_shell("python3 ./tools/record_clip_queue.py tmp/clip_queue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-token-do-not-log", result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.work / "http_calls.txt"
        return path.read_text().splitlines() if path.exists() else []

    def receipt(self):
        return json.loads(self.receipt_path.read_text())

    def test_record_clip_keeps_public_id_url_separate_from_record_confirmation(self):
        self.process_record()
        row = self.receipt()
        self.assertEqual(row["phase"], "ready")
        self.assertEqual(row["clip_id"], "TestClip")
        self.assertEqual(row["clip_url"], "https://clips.twitch.tv/TestClip")
        self.assertNotIn("token", self.receipt_path.read_text())
        self.assertTrue((self.work / "tmp/clip_queue/done" / self.event_path.name).exists())

    def test_delayed_confirmation_restarts_get_only_and_posts_once(self):
        self.process_record("unconfirmed")
        self.assertEqual(self.receipt()["phase"], "accepted")
        self.assertEqual(self.calls(), ["POST", "GET"])
        self.assertTrue(self.event_path.exists())
        self.process_record()
        self.assertEqual(self.calls(), ["POST", "GET", "GET"])
        self.assertEqual(self.receipt()["phase"], "ready")
        self.assertEqual(len((self.work / "chat.txt").read_text().splitlines()), 1)
        # Crash after enqueue but before the producer's ACK: same event returns.
        shutil.copy(self.work / "tmp/clip_queue/done" / self.event_path.name, self.event_path)
        self.process_record()
        self.assertEqual(self.calls(), ["POST", "GET", "GET"])
        self.assertEqual(len((self.work / "chat.txt").read_text().splitlines()), 1)

    def test_auth_loss_after_acceptance_retains_get_only_recovery(self):
        self.process_record("unconfirmed")
        token = self.env.pop("TWITCH_CLIP_TOKEN")
        self.process_record()
        self.assertEqual(self.receipt()["phase"], "accepted")
        self.assertEqual(self.calls(), ["POST", "GET"])
        self.assertTrue(self.event_path.exists())
        self.env["TWITCH_CLIP_TOKEN"] = token
        self.process_record()
        self.assertEqual(self.calls(), ["POST", "GET", "GET"])
        self.assertEqual(self.receipt()["phase"], "ready")

    def test_response_loss_retains_unknown_and_never_reposts(self):
        self.process_record("timeout")
        self.assertEqual(self.receipt()["phase"], "unknown")
        self.assertEqual(self.calls(), ["POST"])
        shutil.copy(self.work / "tmp/clip_queue/failed" / self.event_path.name, self.event_path)
        self.process_record()
        self.assertEqual(self.calls(), ["POST"])

    def test_crash_during_create_is_not_safe_to_retry(self):
        self.receipt_path.parent.mkdir()
        self.receipt_path.write_text(json.dumps(dict(schema=1, phase="creating", clip_id="", clip_url="")))
        self.process_record()
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.work / "tmp/clip_queue/failed" / self.event_path.name).exists())

    def test_stale_new_event_expires_without_clipping_another_game(self):
        row = json.loads(self.event_path.read_text())
        row["created_at"] -= 30
        self.event_path.write_text(json.dumps(row))
        self.process_record()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.receipt()["phase"], "expired")

    def test_rate_limit_is_deferred_bounded_and_never_success(self):
        self.process_record("rate_limit")
        self.assertEqual(self.receipt()["phase"], "retryable")
        self.process_record("rate_limit")
        self.assertEqual(self.calls(), ["POST"])
        # Synthetic time only: make the retry due, without sleeping.
        for _ in range(2):
            row = self.receipt()
            row["updated_at"] -= 6
            self.receipt_path.write_text(json.dumps(row))
            self.process_record("rate_limit")
        row = self.receipt()
        self.assertEqual(row["post_attempts"], 3)
        row["updated_at"] -= 6
        self.receipt_path.write_text(json.dumps(row))
        self.process_record("rate_limit")
        self.assertEqual(self.receipt()["phase"], "rejected")
        self.assertEqual(self.calls(), ["POST"] * 3)
        self.assertFalse((self.work / "chat.txt").exists())

    def test_disabled_and_explore_record_events_never_contact_twitch(self):
        self.env["EXPLORE_MODE"] = "1"
        self.process_record()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.receipt()["phase"], "disabled")

    def test_bad_receipt_and_path_cannot_issue_post(self):
        self.receipt_path.parent.mkdir()
        self.receipt_path.write_text('{"schema":1,"phase":"accepted","clip_id":"../bad"}')
        self.process_record()
        self.assertEqual(self.calls(), [])

    def test_short_interval_updates_remain_distinct_and_bounded_per_tick(self):
        row = json.loads(self.event_path.read_text())
        for key in "bcd":
            second = self.event_path.with_name("record_" + key * 64 + ".json")
            row["event_id"] = key * 64
            second.write_text(json.dumps(row))
        self.process_record()
        self.assertEqual(self.calls().count("POST"), 3)
        self.assertEqual(len(list(self.event_path.parent.glob("record_*.json"))), 1)
        self.process_record()
        self.assertEqual(self.calls().count("POST"), 4)

    def test_accepted_unconfirmed_event_is_preserved_after_bounded_reconciliation(self):
        self.process_record("unconfirmed")
        row = self.receipt()
        row["created_at"] -= 700
        self.receipt_path.write_text(json.dumps(row))
        self.process_record()
        self.assertEqual(self.receipt()["phase"], "unconfirmed")
        self.assertEqual(self.receipt()["clip_id"], "TestClip")
        self.assertEqual(self.calls(), ["POST", "GET"])
