"""Supervisor respawn gate: improve.lock must not stop gameplay alone.

Issue #253: failed_no_apply + retry/backoff wait (running=false) must allow
soren_loop respawn, while a really-running improvement (apply/evaluation)
keeps the exclusion. Fail-safe: malformed state blocks.
"""
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SupervisorImproveGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tmp").mkdir()
        self.env = dict(os.environ)
        self.env["IMPROVE_LOCK_FILE"] = str(self.root / "tmp" / "improve.lock")
        self.env["IMPROVE_STATE_FILE"] = str(self.root / "tmp" / "state" / "improve_state.json")
        self.env["TMP_STATE_DIR"] = str(self.root / "tmp" / "state")
        self.env["WILDCARD_PARALLEL_STATUS_FILE"] = str(
            self.root / "tmp" / "state" / "wildcard_parallel_status.json"
        )
        self.env["IMPROVE_GATE_FRESH_SEC"] = "1800"
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        self.tmp.cleanup()

    def run_gate(self):
        script = (
            "source \"$SN253_ROOT/strategy/improve.sh\" 2>/dev/null || true; "
            "source \"$SN253_ROOT/lib/supervisor_improve_gate.sh\"; "
            "gameplay_blocked_by_improvement"
        )
        env = dict(self.env)
        env["SN253_ROOT"] = str(REPO_ROOT)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_lock(self):
        (self.root / "tmp" / "improve.lock").write_text("{}\n", encoding="utf-8")

    def write_state(self, **fields):
        path = Path(self.env["IMPROVE_STATE_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        base = {"status": "idle", "phase": "", "pid": 0,
                "updated_at": time.time(), "started_at": time.time()}
        base.update(fields)
        path.write_text(json.dumps(base), encoding="utf-8")

    def spawn_fake_improver(self):
        proc = subprocess.Popen(
            ["bash", "-c", "exec -a eloop_improve.sh sleep 300"],
        )
        self.procs.append(proc)
        time.sleep(0.3)
        return proc.pid

    def gate_verdict(self):
        result = self.run_gate()
        return result.returncode, (result.stdout or "").strip()

    def test_active_improvement_blocks_respawn(self):
        """1. active improvement + lock + loop dead -> do not start."""
        self.write_lock()
        pid = self.spawn_fake_improver()
        self.write_state(status="running", phase="analyzing", pid=pid)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("block"), out)

    def test_failed_no_apply_backoff_allows_respawn(self):
        """2. failed_no_apply + running=false + retry + backoff + lock -> allow."""
        self.write_lock()
        (self.root / "tmp" / "state").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp" / "state" / "rate_limit_backoff").write_text(
            "1\n1234567890\nno_apply\n", encoding="utf-8")
        (self.root / "tmp" / "state" / "improve_retry_batch.json").write_text(
            json.dumps({"files": []}), encoding="utf-8")
        self.write_state(status="idle", phase="failed_no_apply", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.startswith("allow"), out)

    def test_retry_pending_without_running_allows_gameplay(self):
        """3. retry pending but nothing running -> do not keep gameplay down."""
        self.write_lock()
        (self.root / "tmp" / "state").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp" / "state" / "improve_retry_batch.json").write_text(
            json.dumps({"files": []}), encoding="utf-8")
        self.write_state(status="idle", phase="failed_no_apply", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 1, out)

    def test_apply_in_progress_blocks(self):
        """4. apply / runtime mutation in progress -> do not start gameplay."""
        self.write_lock()
        pid = self.spawn_fake_improver()
        self.write_state(status="running", phase="applying", pid=pid)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("block"), out)

    def test_malformed_state_fails_safe(self):
        """5. malformed / inconsistent state -> fail-safe block."""
        self.write_lock()
        path = Path(self.env["IMPROVE_STATE_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken json", encoding="utf-8")
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)
        # running=true with unknown phase also blocks.
        self.write_state(status="running", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)

    def test_no_lock_allows_normal_start(self):
        """6. no improve.lock -> normal start path (allow)."""
        self.write_state(status="idle", phase="", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.startswith("allow"), out)

    def test_pause_markers_do_not_confuse_gate(self):
        """7. daemon pause and gameplay pause are separate contracts."""
        state_dir = self.root / "tmp" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "improve_daemon.paused").write_text("operator\n", encoding="utf-8")
        (state_dir / "soren_loop.paused").write_text("operator\n", encoding="utf-8")
        # idle + lock: pause files must not flip the verdict to block.
        self.write_lock()
        self.write_state(status="idle", phase="failed_no_apply", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 1, out)
        # live improvement: pause files must not flip the verdict to allow.
        pid = self.spawn_fake_improver()
        self.write_state(status="running", phase="analyzing", pid=pid)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)

    def test_retry_start_reestablishes_exclusion(self):
        """8. when a retry starts, the exclusion holds again."""
        self.write_lock()
        self.write_state(status="idle", phase="failed_no_apply", pid=0)
        rc, _ = self.gate_verdict()
        self.assertEqual(rc, 1)
        pid = self.spawn_fake_improver()
        self.write_state(status="running", phase="analyzing", pid=pid)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("block"), out)

    def test_wildcard_parallel_blocks(self):
        """Wildcard param trials in flight keep the exclusion."""
        controller = subprocess.Popen(["sleep", "300"])
        self.procs.append(controller)
        time.sleep(0.2)
        self.write_lock()
        (self.root / "tmp" / "state").mkdir(parents=True, exist_ok=True)
        Path(self.env["WILDCARD_PARALLEL_STATUS_FILE"]).write_text(json.dumps({
            "phase": "generating",
            "started_at": int(time.time()),
            "controller_pid": controller.pid,
        }), encoding="utf-8")
        self.write_state(status="idle", phase="", pid=0)
        rc, out = self.gate_verdict()
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
