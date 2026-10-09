"""Contract tests for lib/mute_flag.py, the local-BGM mute ownership record.

The record replaces the old bare ``touch`` flag: ``soviet_local.mjs`` skips every
page interaction while ``tmp/mute_local_bgm`` exists, so a flag left behind by a
dead soren91 session used to stop the broadcast permanently.  These tests pin the
fail-closed release contract:

* only a provably stale record (token+revision+browser_id match, armed, every
  owner dead, no foreign CDP page) may be reaped, atomically by rename;
* legacy / corrupt / unarmed / mismatching / unprovable records are kept;
* no mtime or heartbeat deadline is consulted anywhere.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "lib" / "mute_flag.py"

spec = importlib.util.spec_from_file_location("mute_flag", HELPER)
assert spec and spec.loader
mf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf)


def spawn_sleep():
    """A real child process we can SIGKILL for the reap tests."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])


class MuteFlagTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.flag = os.path.join(self._tmp.name, "mute_local_bgm")
        self.addCleanup(self._kill_children)
        self._children = []

    def _kill_children(self):
        for child in self._children:
            if child.poll() is None:
                child.kill()
                child.wait()

    def owner_pid(self):
        child = spawn_sleep()
        self._children.append(child)
        return child.pid

    def write_raw(self, text):
        with open(self.flag, "w", encoding="utf-8") as handle:
            handle.write(text)

    def read_raw(self):
        with open(self.flag, "r", encoding="utf-8") as handle:
            return handle.read()

    def reaped_files(self, suffix):
        directory = os.path.dirname(self.flag)
        base = os.path.basename(self.flag)
        return [name for name in os.listdir(directory)
                if name.startswith(base + "." + suffix)]


class MissingFlagTests(MuteFlagTestCase):
    def test_status_absent_is_unmuted(self):
        status = mf.cmd_status(self.flag)
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "absent")
        self.assertFalse(status["muted"])
        self.assertFalse(status["exists"])

    def test_reap_absent_reports_nothing_to_do(self):
        result = mf.cmd_reap(self.flag, "t", 1, "b", 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "absent")
        self.assertFalse(result["muted"])

    def test_leave_absent_is_a_no_op(self):
        result = mf.cmd_leave(self.flag, pid=os.getpid(), role="runner")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "absent")

    def test_join_requires_an_existing_record(self):
        result = mf.cmd_join(self.flag, "t", os.getpid(), "runner")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "absent")
        self.assertFalse(os.path.exists(self.flag))


class FailClosedTests(MuteFlagTestCase):
    def test_legacy_empty_touch_flag_is_kept(self):
        self.write_raw("")
        status = mf.cmd_status(self.flag)
        self.assertEqual(status["state"], "legacy")
        self.assertTrue(status["muted"])
        result = mf.cmd_reap(self.flag, "t", 1, "b", 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "legacy")
        self.assertTrue(os.path.exists(self.flag))

    def test_corrupt_records_are_kept(self):
        for blob in ("{not json", "[]", '{"v": 2, "token": "t", "revision": 1, "owners": []}',
                     '{"v": 1, "token": "", "revision": 1, "owners": []}',
                     '{"v": 1, "token": "t", "revision": 0, "owners": []}',
                     '{"v": 1, "token": "t", "revision": 1, "owners": "x"}'):
            with self.subTest(blob=blob):
                self.write_raw(blob)
                status = mf.cmd_status(self.flag)
                self.assertEqual(status["state"], "corrupt")
                self.assertTrue(status["muted"])
                self.assertFalse(mf.cmd_reap(self.flag, "t", 1, "b", 0)["ok"])

    def test_null_browser_id_can_never_be_reaped(self):
        mf.cmd_begin(self.flag, token="t1")
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        self._children[-1].kill()
        self._children[-1].wait()
        result = mf.cmd_reap(self.flag, "t1", 1, "", 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "browser-mismatch")
        self.assertTrue(os.path.exists(self.flag))

    def test_unarmed_record_is_kept(self):
        mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        status = mf.cmd_status(self.flag)
        self.assertFalse(status["armed"])
        self.assertFalse(status["reapable"])
        result = mf.cmd_reap(self.flag, "t1", 1, "bid", 0)
        self.assertEqual(result["reason"], "unarmed")
        self.assertTrue(os.path.exists(self.flag))


class BeginJoinLeaveTests(MuteFlagTestCase):
    def test_begin_writes_an_armed_false_record(self):
        result = mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], 1)
        record = json.loads(self.read_raw())
        self.assertEqual(record["v"], mf.RECORD_VERSION)
        self.assertEqual(record["token"], "t1")
        self.assertFalse(record["armed"])
        self.assertEqual(record["owners"], [])
        self.assertTrue(os.path.exists(self.flag + ".lock"))

    def test_join_arms_and_records_identity(self):
        mf.cmd_begin(self.flag, token="t1")
        pid = self.owner_pid()
        result = mf.cmd_join(self.flag, "t1", pid, "runner", browser_id="bid")
        self.assertTrue(result["ok"])
        self.assertTrue(result["armed"])
        # A browser_id learned by the runner upgrades a control-side null.
        self.assertEqual(result["browser_id"], "bid")
        status = mf.cmd_status(self.flag)
        self.assertTrue(status["armed"])
        self.assertEqual(status["owners"], [{"role": "runner", "pid": pid, "state": "alive"}])
        self.assertFalse(status["all_owners_dead"])
        self.assertFalse(status["reapable"])

    def test_join_rejects_a_stale_token(self):
        mf.cmd_begin(self.flag, token="t1")
        result = mf.cmd_join(self.flag, "other", os.getpid(), "runner")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stale-token")
        self.assertEqual(mf.cmd_status(self.flag)["owners"], [])

    def test_join_rejects_a_dead_owner_pid(self):
        mf.cmd_begin(self.flag, token="t1")
        child = spawn_sleep()
        child.kill()
        child.wait()
        result = mf.cmd_join(self.flag, "t1", child.pid, "runner")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "dead-owner")

    def test_leave_keeps_the_record_while_another_owner_lives(self):
        mf.cmd_begin(self.flag, token="t1")
        runner = self.owner_pid()
        main = self.owner_pid()
        mf.cmd_join(self.flag, "t1", runner, "runner")
        mf.cmd_join(self.flag, "t1", main, "main")
        result = mf.cmd_leave(self.flag, pid=runner, role="runner", token="t1")
        self.assertTrue(result["ok"])
        self.assertFalse(result["released"])
        self.assertTrue(os.path.exists(self.flag))
        self.assertEqual([owner["role"] for owner in result["owners"]], ["main"])
        # ... and the last owner leaving releases the flag.
        final = mf.cmd_leave(self.flag, pid=main, role="main", token="t1")
        self.assertTrue(final["released"])
        self.assertFalse(os.path.exists(self.flag))
        self.assertEqual(len(self.reaped_files("released")), 1)

    def test_leave_with_a_stale_token_keeps_the_record(self):
        mf.cmd_begin(self.flag, token="t1")
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        result = mf.cmd_leave(self.flag, pid=pid, role="runner", token="other")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stale-token")
        self.assertTrue(os.path.exists(self.flag))

    def test_leave_unknown_owner_keeps_the_record(self):
        mf.cmd_begin(self.flag, token="t1")
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        result = mf.cmd_leave(self.flag, pid=os.getpid(), role="main", token="t1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not-an-owner")
        self.assertTrue(os.path.exists(self.flag))


class AbortTests(MuteFlagTestCase):
    def test_abort_releases_an_unarmed_record(self):
        mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        result = mf.cmd_abort(self.flag, token="t1")
        self.assertTrue(result["released"])
        self.assertFalse(os.path.exists(self.flag))
        self.assertEqual(len(self.reaped_files("aborted")), 1)

    def test_abort_refuses_once_an_owner_joined(self):
        mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        result = mf.cmd_abort(self.flag, token="t1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "armed")
        self.assertTrue(os.path.exists(self.flag))

    def test_abort_refuses_legacy_and_stale_tokens(self):
        self.write_raw("")
        self.assertFalse(mf.cmd_abort(self.flag, token="t1")["ok"])
        self.assertTrue(os.path.exists(self.flag))
        mf.cmd_begin(self.flag, token="t1")
        self.assertFalse(mf.cmd_abort(self.flag, token="other")["ok"])
        self.assertTrue(os.path.exists(self.flag))


class ReapTests(MuteFlagTestCase):
    def _dead_owner_record(self, browser_id="bid"):
        mf.cmd_begin(self.flag, token="t1", browser_id=browser_id)
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        self._children[-1].kill()
        self._children[-1].wait()
        return pid

    def test_sigkilled_owner_allows_a_reap(self):
        self._dead_owner_record()
        status = mf.cmd_status(self.flag)
        self.assertEqual(status["state"], "owned")
        self.assertTrue(status["muted"], "the record must stay muted until it is reaped")
        self.assertTrue(status["all_owners_dead"])
        self.assertTrue(status["reapable"])
        result = mf.cmd_reap(self.flag, "t1", 1, "bid", 0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "reaped")
        self.assertFalse(result["muted"])
        self.assertFalse(os.path.exists(self.flag))
        evidence = self.reaped_files("reaped")
        self.assertEqual(len(evidence), 1)
        record = json.loads(Path(os.path.dirname(self.flag), evidence[0]).read_text())
        self.assertEqual(record["token"], "t1")

    def test_reap_requires_foreign_page_count_zero(self):
        self._dead_owner_record()
        result = mf.cmd_reap(self.flag, "t1", 1, "bid", 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "foreign-pages")
        self.assertTrue(os.path.exists(self.flag))

    def test_reap_is_fail_closed_on_mismatch(self):
        self._dead_owner_record()
        for token, revision, browser_id in (("t2", 1, "bid"),
                                            ("t1", 2, "bid"),
                                            ("t1", 1, "other")):
            with self.subTest(token=token, revision=revision, browser_id=browser_id):
                result = mf.cmd_reap(self.flag, token, revision, browser_id, 0)
                self.assertFalse(result["ok"])
                self.assertIn(result["reason"], ("stale", "browser-mismatch"))
                self.assertTrue(os.path.exists(self.flag))

    def test_replacement_generation_defeats_a_stale_decision(self):
        self._dead_owner_record()
        stale = mf.cmd_status(self.flag)
        # A new soren91 generation replaces the record: new token, revision and
        # a live owner.
        second = mf.cmd_begin(self.flag, token="t2", browser_id="bid")
        self.assertEqual(second["revision"], 2)
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t2", pid, "runner")
        result = mf.cmd_reap(self.flag, stale["token"], stale["revision"],
                             stale["browser_id"], 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stale")
        self.assertTrue(os.path.exists(self.flag))

    def test_a_live_owner_blocks_the_reap(self):
        mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        result = mf.cmd_reap(self.flag, "t1", 1, "bid", 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "owner-alive")
        self.assertTrue(os.path.exists(self.flag))

    def test_reap_is_never_an_unlink(self):
        self._dead_owner_record()
        mf.cmd_reap(self.flag, "t1", 1, "bid", 0)
        self.assertFalse(os.path.exists(self.flag))
        # The record body survives inside the renamed evidence file.
        evidence = self.reaped_files("reaped")[0]
        self.assertIn("t1", Path(os.path.dirname(self.flag), evidence).read_text())


class OwnerIdentityTests(MuteFlagTestCase):
    def test_probe_identity_is_stable_for_a_live_pid(self):
        pid = self.owner_pid()
        first = mf.probe_identity(pid)
        second = mf.probe_identity(pid)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertIn(first["kind"], ("linux-proc", "ps-lstart"))
        self.assertIsNone(mf.probe_identity(0))
        self.assertIsNone(mf.probe_identity(-1))

    def test_matching_identity_is_alive_and_a_reused_pid_is_dead(self):
        pid = self.owner_pid()
        identity = mf.probe_identity(pid)
        self.assertEqual(mf.owner_state({"pid": pid, "identity": identity}), "alive")
        tampered = dict(identity)
        if identity["kind"] == "linux-proc":
            tampered["start_ticks"] = identity["start_ticks"] + 1
        else:
            tampered["lstart"] = identity["lstart"] + " (reused)"
        self.assertEqual(mf.owner_state({"pid": pid, "identity": tampered}), "dead")

    def test_a_live_pid_without_a_recorded_identity_is_unknown(self):
        pid = self.owner_pid()
        self.assertEqual(mf.owner_state({"pid": pid, "identity": None}), "unknown")
        self.assertEqual(mf.owner_state({"pid": 0, "identity": None}), "unknown")

    def test_a_gone_pid_is_dead_even_without_an_identity(self):
        child = spawn_sleep()
        child.kill()
        child.wait()
        self.assertEqual(mf.owner_state({"pid": child.pid, "identity": None}), "dead")
        self.assertEqual(mf.owner_state({"pid": child.pid, "identity": None}), "dead")


class BrowserIdTests(MuteFlagTestCase):
    def test_browser_id_is_the_cdp_websocket_pathname(self):
        expected = "/devtools/browser/9d0f6f0f-test"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server naming
                if self.path != "/json/version":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({
                    "Browser": "Chrome/145.0",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222" + expected,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # silence the test server
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        url = "http://127.0.0.1:%d" % server.server_port
        self.assertEqual(mf.fetch_browser_id(url), expected)
        # A trailing slash must not change the identity.
        self.assertEqual(mf.fetch_browser_id(url + "/"), expected)


class ConcurrencyTests(MuteFlagTestCase):
    def test_parallel_joins_keep_every_owner(self):
        mf.cmd_begin(self.flag, token="t1", browser_id="bid")
        pids = [self.owner_pid() for _ in range(4)]
        processes = [
            subprocess.Popen([sys.executable, str(HELPER), "join", "--flag", self.flag,
                              "--token", "t1", "--role", "runner", "--pid", str(pid)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for pid in pids
        ]
        for process in processes:
            self.assertEqual(process.wait(timeout=30), 0)
        status = mf.cmd_status(self.flag)
        self.assertEqual(sorted(owner["pid"] for owner in status["owners"]), sorted(pids))
        self.assertTrue(status["armed"])

    def test_lock_file_is_never_removed(self):
        lock = self.flag + ".lock"
        mf.cmd_begin(self.flag, token="t1")
        self.assertTrue(os.path.exists(lock))
        pid = self.owner_pid()
        mf.cmd_join(self.flag, "t1", pid, "runner")
        mf.cmd_leave(self.flag, pid=pid, role="runner")
        self.assertFalse(os.path.exists(self.flag))
        self.assertTrue(os.path.exists(lock), "the flock file must outlive the record")


class CliTests(MuteFlagTestCase):
    def run_cli(self, *args):
        """Exercise the real subprocess entry point (arg order included)."""
        result = subprocess.run(
            [sys.executable, str(HELPER)] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout.decode("utf-8"))

    def test_cli_begin_status_reap_round_trip(self):
        begin = self.run_cli("begin", "--flag", self.flag, "--token", "t1",
                             "--browser-id", "bid")
        self.assertEqual(begin["revision"], 1)
        pid = self.owner_pid()
        join = self.run_cli("join", "--flag", self.flag, "--token", "t1",
                            "--role", "runner", "--pid", str(pid))
        self.assertTrue(join["armed"])
        self.assertEqual(self.run_cli("status", "--flag", self.flag)["state"], "owned")
        self._children[-1].kill()
        self._children[-1].wait()
        reap = self.run_cli("reap", "--flag", self.flag, "--token", "t1",
                            "--revision", "1", "--browser-id", "bid",
                            "--foreign-pages", "0")
        self.assertTrue(reap["ok"], reap)
        self.assertEqual(self.run_cli("status", "--flag", self.flag)["state"], "absent")

    def test_cli_rejects_a_missing_flag_argument(self):
        result = subprocess.run([sys.executable, str(HELPER), "status"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
