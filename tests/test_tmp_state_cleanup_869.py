"""Tests for soviet_now#869 tmp/state orphan cleanup.

Covers the four items from Devin's read-only investigation
(issuecomment-6883346235):

1. improve processes remove their own runtime script + integrity snapshot
   on exit (including abnormal exits).
2. ``<pid>``-suffixed ps snapshots are removed when the writing process exits.
3. supervisor startup sweep reclaims ownerless temp files (allowlist only,
   fail-closed, ``shadow-once-*`` excluded).
4. ``improve_daemon_stall.json`` is marked recovered when the daemon
   (re)starts.

Shell functions are extracted from the real scripts and executed in a
scratch directory, following tests/test_improve_heartbeat.py.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEAD_PID = "2147483647"
OLD_MTIME = time.time() - 2 * 60 * 60


def _extract_function(source, header, end_marker="\n}\n"):
    """Extract a shell function starting with ``header``."""
    rest = source.split(header, 1)[1]
    body = rest.split(end_marker, 1)[0]
    return header + body + "\n}\n"


def _read_script(name):
    return (REPO_ROOT / name).read_text(encoding="utf-8")


class TmpStateCleanup869Tests(unittest.TestCase):
    def _run_bash_file(self, script_text, cwd, timeout=30):
        script = Path(cwd) / "_869_harness.sh"
        script.write_text(script_text, encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc

    @staticmethod
    def _make_state_dir(root):
        state = Path(root) / "tmp" / "state"
        state.mkdir(parents=True, exist_ok=True)
        return state

    @staticmethod
    def _age(path):
        os.utime(path, (OLD_MTIME, OLD_MTIME))

    @staticmethod
    def _spawn_named(argv0):
        """Spawn a sleeper whose cmdline starts with ``argv0``."""
        return subprocess.Popen(
            ["bash", "-c", 'exec -a "$1" sleep 30', "bash", argv0],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _kill(proc):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # --- Item 1: eloop_improve.sh EXIT trap ---------------------------

    def test_eloop_cleanup_removes_own_runtime_and_integrity_files(self):
        fn = _extract_function(
            _read_script("eloop_improve.sh"), "_improve_cleanup_active_ai() {"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            runtime = state / "eloop_improve_runtime.ABCDEF.sh"
            integrity = state / "host_integrity_before.ABCDEF"
            runtime.write_text("# runtime\n", encoding="utf-8")
            integrity.write_text("snapshot\n", encoding="utf-8")
            script = (
                "_stop_loop_descendants() { :; }\n"
                "_stop_pid_with_fallback() { :; }\n"
                + fn
                + "SCRIPT_DIR=" + shlex.quote(str(root)) + "\n"
                + "HOST_ROOT=" + shlex.quote(str(root)) + "\n"
                + "TMP_STATE_DIR=tmp/state\n"
                + "RUN_CMD_ACTIVE_PID=0\n"
                + "ELOOP_RUNTIME_SCRIPT_FILE=" + shlex.quote(str(runtime)) + "\n"
                + "HOST_INTEGRITY_BEFORE_FILE=" + shlex.quote(str(integrity)) + "\n"
                + "_improve_cleanup_active_ai\n"
            )
            proc = self._run_bash_file(script, root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(runtime.exists(), "runtime script was not removed")
            self.assertFalse(integrity.exists(), "integrity snapshot was not removed")

    def test_eloop_cleanup_keeps_paths_outside_the_guarded_pattern(self):
        fn = _extract_function(
            _read_script("eloop_improve.sh"), "_improve_cleanup_active_ai() {"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Fallback source script (cp failed) and an integrity file in an
            # unexpected directory must never be deleted by the trap.
            source_script = root / "eloop_improve.sh"
            foreign_integrity = root / "elsewhere" / "host_integrity_before.X"
            foreign_integrity.parent.mkdir(parents=True)
            source_script.write_text("# source\n", encoding="utf-8")
            foreign_integrity.write_text("snapshot\n", encoding="utf-8")
            script = (
                "_stop_loop_descendants() { :; }\n"
                "_stop_pid_with_fallback() { :; }\n"
                + fn
                + "SCRIPT_DIR=" + shlex.quote(str(root)) + "\n"
                + "HOST_ROOT=" + shlex.quote(str(root)) + "\n"
                + "TMP_STATE_DIR=tmp/state\n"
                + "RUN_CMD_ACTIVE_PID=0\n"
                + "ELOOP_RUNTIME_SCRIPT_FILE=" + shlex.quote(str(source_script)) + "\n"
                + "HOST_INTEGRITY_BEFORE_FILE="
                + shlex.quote(str(foreign_integrity)) + "\n"
                + "_improve_cleanup_active_ai\n"
            )
            proc = self._run_bash_file(script, root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(source_script.exists(), "source script was deleted")
            self.assertTrue(
                foreign_integrity.exists(), "foreign integrity file was deleted"
            )

    def test_improve_launch_failure_path_removes_runtime_script(self):
        source = _read_script("strategy/improve.sh")
        idx = source.index("起動失敗")
        block = source[idx:idx + 600]
        self.assertIn(
            '"$runtime_root"/tmp/state/eloop_improve_runtime.*.sh)'
            ' rm -f "$runtime_script"',
            block,
        )
        self.assertLess(
            block.index('rm -f "$runtime_script"'),
            block.index("_release_spawn_lock"),
        )

    # --- Item 2: <pid> ps snapshots -----------------------------------

    def test_start_all_cleanup_removes_own_ps_snapshot(self):
        fn = _extract_function(_read_script("start_all.sh"), "_cleanup() {")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            script = (
                "_log() { :; }\n"
                "_stop_all_workers() { :; }\n"
                + fn
                + "PID_FILE=" + shlex.quote(str(root / "pidfile")) + "\n"
                + "SUPERVISOR_STOP_REQUESTED=0\n"
                + "printf '%s' \"$$\" > \"$PID_FILE\"\n"
                + "touch \"tmp/state/worker_duplicates.ps.$$.txt\"\n"
                + "_cleanup\n"
                + "test ! -e \"tmp/state/worker_duplicates.ps.$$.txt\"\n"
            )
            proc = self._run_bash_file(script, root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(list(state.glob("worker_duplicates.ps.*.txt")))

    def test_show_status_trap_removes_ps_snapshot(self):
        source = _read_script("show_status.sh")
        trap_lines = [
            line
            for line in source.splitlines()
            if line.startswith("trap ") and "show_status_worker_ps" in line
        ]
        self.assertEqual(len(trap_lines), 1, "expected exactly one trap line")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_state_dir(root)
            script = (
                trap_lines[0] + "\n"
                + "touch \"tmp/state/show_status_worker_ps.$$.txt\"\n"
                + "exit 0\n"
            )
            proc = self._run_bash_file(script, root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            state = root / "tmp" / "state"
            self.assertFalse(list(state.glob("show_status_worker_ps.*.txt")))

    # --- Item 3: startup orphan sweep ---------------------------------

    def _sweep_harness(self):
        fn = _extract_function(
            _read_script("start_all.sh"), "_cleanup_orphan_tmp_state_files() {"
        )
        return "_log() { :; }\n" + fn + "_cleanup_orphan_tmp_state_files\n"

    def test_orphan_sweep_ps_snapshots(self):
        live = self._spawn_named("start_all.sh")
        reused = subprocess.Popen(
            ["sleep", "30"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = self._make_state_dir(root)
                dead = state / f"worker_duplicates.ps.{DEAD_PID}.txt"
                live_file = state / f"worker_duplicates.ps.{live.pid}.txt"
                reused_file = state / f"show_status_worker_ps.{reused.pid}.txt"
                fresh_dead = state / "worker_duplicates.ps.2147483646.txt"
                for path in (dead, live_file, reused_file, fresh_dead):
                    path.write_text("ps\n", encoding="utf-8")
                for path in (dead, live_file, reused_file):
                    self._age(path)
                # shadow-once-* and unrelated files are never touched.
                shadow_dir = state / "shadow-once-abc123"
                shadow_dir.mkdir()
                (shadow_dir / "worker.pid").write_text("1\n", encoding="utf-8")
                shadow_manifest = state / "shadow-once-input-x.json"
                shadow_manifest.write_text("{}\n", encoding="utf-8")
                self._age(shadow_manifest)
                paused = state / "improve_daemon.paused"
                paused.write_text("", encoding="utf-8")
                self._age(paused)
                stall = state / "improve_daemon_stall.json"
                stall.write_text('{"status":"stalled"}\n', encoding="utf-8")
                self._age(stall)

                proc = self._run_bash_file(self._sweep_harness(), root)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertFalse(dead.exists(), "dead pid snapshot was kept")
                self.assertTrue(live_file.exists(), "live pid snapshot was removed")
                self.assertFalse(
                    reused_file.exists(), "reused pid snapshot was kept"
                )
                self.assertTrue(fresh_dead.exists(), "fresh snapshot was removed")
                self.assertTrue(shadow_dir.exists(), "shadow-once dir was removed")
                self.assertTrue(
                    shadow_manifest.exists(), "shadow-once manifest was removed"
                )
                self.assertTrue(paused.exists(), "paused marker was removed")
                self.assertTrue(stall.exists(), "stall file was removed by sweep")
        finally:
            self._kill(live)
            self._kill(reused)

    def test_orphan_sweep_runtime_and_integrity_without_live_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            runtime = state / "eloop_improve_runtime.T869TEST.sh"
            integrity = state / "host_integrity_before.T869TEST"
            runtime.write_text("# runtime\n", encoding="utf-8")
            integrity.write_text("snapshot\n", encoding="utf-8")
            self._age(runtime)
            self._age(integrity)
            # A transient process mentioning eloop_improve only delays the
            # sweep (fail-closed); retry a few times.
            for _ in range(3):
                proc = self._run_bash_file(self._sweep_harness(), root)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if not runtime.exists() and not integrity.exists():
                    break
                time.sleep(0.5)
            self.assertFalse(runtime.exists(), "orphan runtime script was kept")
            self.assertFalse(integrity.exists(), "orphan integrity file was kept")

    def test_orphan_sweep_keeps_files_with_live_worker(self):
        runtime_name = "eloop_improve_runtime.T869LIVE.sh"
        worker = self._spawn_named(f"bash {runtime_name}")
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = self._make_state_dir(root)
                runtime = state / runtime_name
                integrity = state / "host_integrity_before.T869LIVE"
                runtime.write_text("# runtime\n", encoding="utf-8")
                integrity.write_text("snapshot\n", encoding="utf-8")
                self._age(runtime)
                self._age(integrity)
                proc = self._run_bash_file(self._sweep_harness(), root)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(runtime.exists(), "live runtime script was removed")
                self.assertTrue(integrity.exists(), "integrity file was removed")
        finally:
            self._kill(worker)

    # --- Item 4: improve_daemon_stall.json recovery --------------------

    def _recovered_harness(self):
        fn = _extract_function(
            _read_script("improve_daemon.sh"),
            "_mark_improve_daemon_recovered() {",
            "\n}\n_mark_improve_daemon_recovered",
        )
        return fn + "_mark_improve_daemon_recovered\n"

    def test_improve_daemon_marks_stall_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            stall = state / "improve_daemon_stall.json"
            stall.write_text(
                '{"status":"stalled","updated_at":1757000000,'
                '"pid":1234,"detail":"x"}\n',
                encoding="utf-8",
            )
            proc = self._run_bash_file(self._recovered_harness(), root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(stall.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "recovered")
            self.assertEqual(data["stalled_at"], 1757000000)
            self.assertEqual(data["pid"], 1234)
            self.assertIn("recovered_at", data)

    def test_improve_daemon_recovered_marking_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            stall = state / "improve_daemon_stall.json"
            stall.write_text(
                '{"status":"recovered","updated_at":1757000100,'
                '"stalled_at":1757000000,"recovered_at":1757000100}\n',
                encoding="utf-8",
            )
            before = stall.read_text(encoding="utf-8")
            proc = self._run_bash_file(self._recovered_harness(), root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(stall.read_text(encoding="utf-8"), before)

    def test_improve_daemon_recovered_marking_missing_or_broken_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._make_state_dir(root)
            proc = self._run_bash_file(self._recovered_harness(), root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            broken = state / "improve_daemon_stall.json"
            broken.write_text("not json\n", encoding="utf-8")
            proc = self._run_bash_file(self._recovered_harness(), root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(broken.read_text(encoding="utf-8"), "not json\n")


if __name__ == "__main__":
    unittest.main()
