"""Real local queue/script/receipt paths with a synthetic HTTP executable."""
import json
from pathlib import Path
import re
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

    def process_record(self, mode="success", *, now=None):
        self.env["HTTP_TEST_MODE"] = mode
        command = "python3 ./tools/record_clip_queue.py tmp/clip_queue"
        if now is not None:
            self.env["RECORD_TEST_NOW"] = str(now)
            command = '''PYTHONPATH=tools python3 -c 'import os; from record_clip_queue import process; process("tmp/clip_queue", now=float(os.environ["RECORD_TEST_NOW"]))' '''
        result = self.run_shell(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-token-do-not-log", result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.work / "http_calls.txt"
        return path.read_text().splitlines() if path.exists() else []

    def receipt(self):
        return json.loads(self.receipt_path.read_text())

    def protect_event(self):
        target = self.event_path.parent / "record_pending" / self.event_path.name
        target.parent.mkdir(exist_ok=True)
        self.event_path.rename(target)
        self.event_path = target

    def test_old_resident_cannot_consume_protected_event_then_hot_reload_uses_receipt(self):
        self.protect_event()
        shutil.copy(ROOT / "tests/fixtures/record-clips/legacy_chat_clip_queue.sh",
                    self.work / "legacy_clip_queue.sh")
        shutil.copy(ROOT / "lib/chat_clip_queue.sh", self.work / "lib/chat_clip_queue.sh")
        # Execute the exact chat-only loader from the real per-tick shim. Other
        # modules/services are omitted; all HTTP goes through the fixture stub.
        loader = re.search(r'(?ms)^if \[ "\$\{WORKER_NAME:-\}" = "chat_worker" \]; then\n.*?^fi',
                           (ROOT / "eloop_lib.sh").read_text()).group()
        (self.work / "hot_loader.sh").write_text(loader)
        (self.work / "tmp/state").mkdir()
        (self.work / "tmp/state/chat_worker.paused").touch()
        result = self.run_shell('''
CLIP_QUEUE_DIR=tmp/clip_queue
CLIP_QUEUE_DONE_DIR=tmp/clip_queue/done
TMP_MARKERS_DIR=tmp/markers
TMP_DEBUG_DIR=tmp/debug
ELOOP_LIB_DIR="$PWD"
_log() { :; }
source ./legacy_clip_queue.sh
before_pid=$$
_process_clip_queue
test ! -e http_calls.txt || exit 10
test -f tmp/clip_queue/record_pending/record_*.json || exit 11
# Loading in another worker must preserve its functions and all queue state.
WORKER_NAME=radio_worker
source ./hot_loader.sh
_process_clip_queue
test ! -e http_calls.txt || exit 12
WORKER_NAME=chat_worker
source ./hot_loader.sh
test "$$" = "$before_pid" || exit 13
test -f tmp/state/chat_worker.paused || exit 14
# Reload itself neither drains the queue nor changes a user's pause marker.
test ! -e http_calls.txt || exit 15
# Model an operator-authorized unpause only inside this disposable sandbox.
rm tmp/state/chat_worker.paused
_process_clip_queue
_process_clip_queue
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-token-do-not-log", result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["POST", "GET"])
        self.assertEqual(self.receipt()["phase"], "ready")
        self.assertEqual(self.receipt()["clip_id"], "TestClip")
        self.assertTrue((self.work / "tmp/clip_queue/done" / self.event_path.name).exists())

    def test_protected_backlog_expires_at_original_capture_time_without_http(self):
        self.protect_event()
        row = json.loads(self.event_path.read_text())
        row["created_at"] = 100
        self.event_path.write_text(json.dumps(row))
        self.process_record(now=121)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.receipt()["phase"], "expired")
        self.assertTrue((self.work / "tmp/clip_queue/done" / self.event_path.name).exists())

    def test_same_id_across_old_and_protected_paths_posts_once_and_recovers_get_only(self):
        old_path = self.event_path
        self.protect_event()
        shutil.copy(self.event_path, old_path)
        self.process_record("unconfirmed")
        self.assertEqual(self.calls().count("POST"), 1)
        self.assertEqual(self.receipt()["phase"], "accepted")
        self.process_record()
        self.assertEqual(self.calls().count("POST"), 1)
        self.assertEqual(self.receipt()["phase"], "ready")
        self.assertFalse(old_path.exists())
        self.assertFalse(self.event_path.exists())

    def test_protected_queue_obeys_stop_and_single_flight_lock(self):
        import fcntl
        self.protect_event()
        stop = self.work / "tmp/stop"
        stop.touch()
        self.process_record()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.event_path.exists())
        stop.unlink()
        with (self.work / "tmp/clip_queue/.record-lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.process_record()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.event_path.exists())

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

    def add_event(self, key, *, age=0, accepted=False):
        row = json.loads(self.event_path.read_text())
        row.update(event_id=key * 64, created_at=time.time() - age)
        path = self.event_path.with_name("record_" + row["event_id"] + ".json")
        path.write_text(json.dumps(row))
        if accepted:
            receipt = self.receipt_path.with_name(row["event_id"] + ".json")
            receipt.parent.mkdir(exist_ok=True)
            receipt.write_text(json.dumps(dict(
                schema=1, phase="accepted", clip_id="TestClip", clip_url="",
                created_at=time.time() - 30, updated_at=time.time() - 10,
                post_attempts=1)))
        return path

    def test_unconfirmed_get_backlog_cannot_starve_a_fresh_post(self):
        # The three dictionary-first accepted entries previously used all slots
        # on every tick. A later fresh entry never received its first POST.
        for key in "abc":
            self.add_event(key, age=30, accepted=True)
        fresh = self.add_event("f", age=19)
        self.process_record("unconfirmed", now=time.time())
        self.assertEqual(self.calls().count("POST"), 1)
        saved = json.loads(self.receipt_path.with_name("f" * 64 + ".json").read_text())
        self.assertEqual(saved["phase"], "accepted")
        self.assertTrue(fresh.exists())
        self.assertEqual(self.calls()[0], "POST")  # before any backlog GET
        self.assertLessEqual(self.calls().count("GET"), 3)

    def test_accepted_gets_rotate_across_ticks_with_fresh_posts(self):
        old_time = time.time() - 10
        for key in "abcd":
            self.add_event(key, age=30, accepted=True)
        self.add_event("e")
        self.add_event("f")
        self.process_record("unconfirmed")
        self.assertEqual(self.calls().count("POST"), 2)
        self.assertEqual(self.calls().count("GET"), 3)
        self.process_record("unconfirmed")
        for key in "abcd":
            saved = json.loads(self.receipt_path.with_name(key * 64 + ".json").read_text())
            self.assertGreater(saved["updated_at"], old_time)
        self.assertEqual(self.calls().count("POST"), 2)
        self.assertEqual(self.calls().count("GET"), 6)

    def test_fresh_posts_use_creation_deadline_not_dictionary_order(self):
        self.add_event("b")
        self.add_event("c")
        self.add_event("f", age=19)
        self.process_record(now=time.time())
        self.assertEqual(self.calls().count("POST"), 3)
        self.assertTrue((self.event_path.parent / "done" / ("record_" + "f" * 64 + ".json")).exists())
        self.assertEqual(len(list(self.event_path.parent.glob("record_*.json"))), 1)

    def test_accepted_unconfirmed_event_is_preserved_after_bounded_reconciliation(self):
        self.process_record("unconfirmed")
        row = self.receipt()
        row["created_at"] -= 700
        self.receipt_path.write_text(json.dumps(row))
        self.process_record()
        self.assertEqual(self.receipt()["phase"], "unconfirmed")
        self.assertEqual(self.receipt()["clip_id"], "TestClip")
        self.assertEqual(self.calls(), ["POST", "GET"])
