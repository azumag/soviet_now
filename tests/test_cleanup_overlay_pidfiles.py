import importlib.util
import os
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
