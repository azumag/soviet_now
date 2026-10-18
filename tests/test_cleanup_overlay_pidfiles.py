import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "lib" / "cleanup_overlay_pidfiles.py"
PREREQS = REPO_ROOT / "wait_soren_runtime_prereqs.sh"

spec = importlib.util.spec_from_file_location("cleanup_overlay_pidfiles", HELPER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CleanupOverlayPidfilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "tmp" / "state"
        self.state.mkdir(parents=True)

    def write_pid(self, name: str, value: str) -> Path:
        path = self.state / name
        path.write_text(value, encoding="ascii")
        path.chmod(0o600)
        return path

    def test_removes_only_dead_allowlisted_pidfiles(self) -> None:
        for name in module.PIDFILE_NAMES:
            self.write_pid(name, "2147483647\n")
        unrelated = self.write_pid("radio_worker.pid", "2147483647\n")

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_overlay_pidfiles(self.root)

        self.assertEqual(result["removed"], 3)
        self.assertTrue(unrelated.exists())
        for name in module.PIDFILE_NAMES:
            self.assertFalse((self.state / name).exists())

    def test_live_pid_is_never_removed(self) -> None:
        path = self.write_pid(module.PIDFILE_NAMES[0], f"{os.getpid()}\n")
        result = module.cleanup_overlay_pidfiles(self.root)
        self.assertEqual(result["skipped_alive"], 1)
        self.assertTrue(path.exists())

    def test_symlinked_state_directory_is_never_followed(self) -> None:
        outside = self.root / "outside-state"
        outside.mkdir()
        protected = outside / module.PIDFILE_NAMES[0]
        protected.write_text("2147483647\n", encoding="ascii")
        protected.chmod(0o600)
        self.state.rmdir()
        self.state.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_overlay_pidfiles(self.root)

        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped_unsafe"], len(module.PIDFILE_NAMES))
        self.assertTrue(protected.exists())

    def test_symlink_and_hardlink_are_never_removed(self) -> None:
        outside = self.root / "outside.pid"
        outside.write_text("2147483647\n", encoding="ascii")
        outside.chmod(0o600)
        symlink = self.state / module.PIDFILE_NAMES[0]
        symlink.symlink_to(outside)

        hard_source = self.root / "hard-source.pid"
        hard_source.write_text("2147483647\n", encoding="ascii")
        hard_source.chmod(0o600)
        hardlink = self.state / module.PIDFILE_NAMES[1]
        os.link(hard_source, hardlink)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_overlay_pidfiles(self.root)

        self.assertGreaterEqual(result["skipped_unsafe"], 2)
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(hardlink.exists())
        self.assertEqual(outside.read_text(encoding="ascii"), "2147483647\n")

    def test_foreign_owner_or_group_writable_file_is_never_removed(self) -> None:
        path = self.write_pid(module.PIDFILE_NAMES[0], "2147483647\n")
        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_overlay_pidfiles(self.root, expected_uid=os.geteuid() + 1)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(path.exists())

        path.chmod(0o620)
        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_overlay_pidfiles(self.root)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(path.exists())

    def test_malformed_pid_is_never_removed(self) -> None:
        path = self.write_pid(module.PIDFILE_NAMES[0], "not-a-pid\n")
        result = module.cleanup_overlay_pidfiles(self.root)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(path.exists())

    def test_replaced_inode_is_never_unlinked(self) -> None:
        path = self.write_pid(module.PIDFILE_NAMES[0], "2147483647\n")
        real_stat = module.os.stat
        calls = 0

        def replacing_stat(name, *args, **kwargs):
            nonlocal calls
            if name == module.PIDFILE_NAMES[0] and kwargs.get("dir_fd") is not None:
                calls += 1
                if calls == 1:
                    replacement = self.state / "replacement.pid"
                    replacement.write_text("2147483647\n", encoding="ascii")
                    replacement.chmod(0o600)
                    os.replace(replacement, path)
            return real_stat(name, *args, **kwargs)

        with mock.patch.object(module, "_pid_is_alive", return_value=False), mock.patch.object(
            module.os, "stat", side_effect=replacing_stat
        ):
            result = module.cleanup_overlay_pidfiles(self.root)

        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(path.exists())

    def test_existing_prereq_hook_runs_cleanup_fail_open_before_runtime_checks(self) -> None:
        source = PREREQS.read_text(encoding="utf-8")
        cleanup = 'python3 "$SCRIPT_DIR/lib/cleanup_overlay_pidfiles.py" >/dev/null 2>&1 || true'
        self.assertIn(cleanup, source)
        self.assertIn('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', source)
        self.assertLess(source.index(cleanup), source.index("for command_name in pactl xdpyinfo"))
        self.assertIn("docich #416", source)
        self.assertIn("pause marker suspends the manual-loop entry", source)


class CleanupManualLoopPidfileTests(unittest.TestCase):
    """docich #416: the legacy auxiliary pidfile converges only when safe."""

    DEAD_PID = "2147483647\n"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "tmp" / "state"
        self.state.mkdir(parents=True)
        self.path = self.state / module.MANUAL_LOOP_PIDFILE

    def write_manual_pid(self, value: str = DEAD_PID) -> Path:
        self.path.write_text(value, encoding="ascii")
        self.path.chmod(0o600)
        return self.path

    def test_pid_dead_after_stop_path_is_removed(self) -> None:
        self.write_manual_pid()
        unrelated = self.state / "explore.pid"
        unrelated.write_text(self.DEAD_PID, encoding="ascii")
        unrelated.chmod(0o600)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["removed"], 1)
        self.assertFalse(self.path.exists())
        self.assertTrue(unrelated.exists())

    def test_live_pidfile_including_a_reused_pid_is_never_removed(self) -> None:
        # A pidfile whose recorded PID is now held by any live process (the
        # classic PID-reuse case) must be preserved.
        self.write_manual_pid(f"{os.getpid()}\n")
        result = module.cleanup_manual_loop_pidfile(self.root)
        self.assertEqual(result["skipped_alive"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(self.path.exists())

    def test_symlinked_pidfile_is_never_removed(self) -> None:
        outside = self.root / "outside.pid"
        outside.write_text(self.DEAD_PID, encoding="ascii")
        outside.chmod(0o600)
        self.path.symlink_to(outside)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(outside.read_text(encoding="ascii"), self.DEAD_PID)

    def test_hardlinked_pidfile_is_never_removed(self) -> None:
        source = self.root / "hard-source.pid"
        source.write_text(self.DEAD_PID, encoding="ascii")
        source.chmod(0o600)
        os.link(source, self.path)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.exists())

    def test_foreign_owner_pidfile_is_never_removed(self) -> None:
        self.write_manual_pid()
        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root, expected_uid=os.geteuid() + 1)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.exists())

    def test_group_writable_pidfile_is_never_removed(self) -> None:
        self.write_manual_pid()
        self.path.chmod(0o620)
        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.exists())

    def test_malformed_pid_is_never_removed(self) -> None:
        self.write_manual_pid("not-a-pid\n")
        result = module.cleanup_manual_loop_pidfile(self.root)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.exists())

    def test_replaced_inode_is_never_unlinked(self) -> None:
        self.write_manual_pid()
        real_stat = module.os.stat
        called = False

        def replacing_stat(name, *args, **kwargs):
            nonlocal called
            if name == module.MANUAL_LOOP_PIDFILE and kwargs.get("dir_fd") is not None and not called:
                called = True
                replacement = self.state / "replacement.pid"
                replacement.write_text(self.DEAD_PID, encoding="ascii")
                replacement.chmod(0o600)
                os.replace(replacement, self.path)
            return real_stat(name, *args, **kwargs)

        with mock.patch.object(module, "_pid_is_alive", return_value=False), mock.patch.object(
            module.os, "stat", side_effect=replacing_stat
        ):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(self.path.exists())

    def test_pause_marker_suspends_convergence(self) -> None:
        self.write_manual_pid()
        marker = self.state / module.MANUAL_LOOP_PAUSE_MARKER
        marker.write_text("{}\n", encoding="ascii")

        with mock.patch.object(module, "_pid_is_alive", return_value=False) as alive:
            result = module.cleanup_manual_loop_pidfile(self.root)

        alive.assert_not_called()
        self.assertEqual(result["skipped_paused"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(self.path.exists())
        self.assertTrue(marker.exists())

    def test_pause_marker_of_any_type_suspends_convergence(self) -> None:
        self.write_manual_pid()
        target = self.root / "operator-owned-pause"
        target.write_text("paused\n", encoding="ascii")
        marker = self.state / module.MANUAL_LOOP_PAUSE_MARKER
        marker.symlink_to(target)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["skipped_paused"], 1)
        self.assertTrue(self.path.exists())

    def test_missing_pidfile_reports_missing(self) -> None:
        result = module.cleanup_manual_loop_pidfile(self.root)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped_paused"], 0)

    def test_symlinked_state_directory_is_never_followed(self) -> None:
        outside = self.root / "outside-state"
        outside.mkdir()
        protected = outside / module.MANUAL_LOOP_PIDFILE
        protected.write_text(self.DEAD_PID, encoding="ascii")
        protected.chmod(0o600)
        self.state.rmdir()
        self.state.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            result = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertTrue(protected.exists())

    def test_paused_manual_loop_entry_does_not_block_overlay_convergence(self) -> None:
        for name in module.PIDFILE_NAMES:
            pidfile = self.state / name
            pidfile.write_text(self.DEAD_PID, encoding="ascii")
            pidfile.chmod(0o600)
        self.write_manual_pid()
        marker = self.state / module.MANUAL_LOOP_PAUSE_MARKER
        marker.write_text("{}\n", encoding="ascii")

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            overlay = module.cleanup_overlay_pidfiles(self.root)
            manual = module.cleanup_manual_loop_pidfile(self.root)

        self.assertEqual(overlay["removed"], 3)
        self.assertEqual(manual["skipped_paused"], 1)
        self.assertEqual(manual["removed"], 0)
        self.assertTrue(self.path.exists())
        self.assertTrue(marker.exists())

    def test_main_reports_fixed_counters_for_both_allowlists(self) -> None:
        self.write_manual_pid()
        for name in module.PIDFILE_NAMES:
            pidfile = self.state / name
            pidfile.write_text(self.DEAD_PID, encoding="ascii")
            pidfile.chmod(0o600)

        with mock.patch.object(module, "_pid_is_alive", return_value=False):
            completed = subprocess.run(
                [sys.executable, str(HELPER), "--root", str(self.root)],
                text=True,
                capture_output=True,
                check=True,
            )

        result = json.loads(completed.stdout)
        self.assertEqual(
            sorted(result),
            ["missing", "removed", "skipped_alive", "skipped_paused", "skipped_unsafe"],
        )
        self.assertEqual(result["removed"], 4)
        self.assertEqual(result["skipped_paused"], 0)


if __name__ == "__main__":
    unittest.main()
