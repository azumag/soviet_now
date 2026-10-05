"""Executable ChatObs cache regressions; no live runtime or network is used."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFRESH = """
import os
from lib.viewer_chat_cache import refresh_viewer_chat_monitor_if_changed
print(refresh_viewer_chat_monitor_if_changed(
    os.environ['VIEWER_CHAT_MONITOR_SOURCE'],
    os.environ['VIEWER_CHAT_MONITOR_FILE'],
    os.environ['VIEWER_CHAT_MONITOR_LOOKBACK']))
"""

# Keep the real shell script and its entire inline producer code. Only its
# Python entry point is instrumented so read/publication interleavings and
# transient errors are deterministic, including when tests run as root.
PRODUCER_ENTRY = r'''
import builtins
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, os.getcwd())
sys.argv = sys.argv[1:]
source, monitor, lookback, mode = sys.argv[1:5]
code = sys.stdin.read()
real_open = builtins.open
with real_open("producer-calls.jsonl", "a", encoding="utf-8") as stream:
    stream.write(json.dumps([source, monitor, lookback, mode]) + "\n")
hook = os.environ.get("CACHE_TEST_HOOK", "")
if hook == "nonzero":
    sys.exit(23)

class Reader:
    def __init__(self, handle):
        self.handle = handle
    def __getattr__(self, name):
        return getattr(self.handle, name)
    def __enter__(self):
        self.handle.__enter__()
        return self
    def __exit__(self, *args):
        return self.handle.__exit__(*args)
    def read(self, *args, **kwargs):
        if hook == "read-error":
            raise PermissionError("synthetic transient source read error")
        data = self.handle.read(*args, **kwargs)
        if hook == "after-read":
            Path("read-done").write_text("ready", encoding="utf-8")
            deadline = time.monotonic() + 5
            while not Path("continue-read").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("test did not release producer read")
                time.sleep(0.005)
        return data

def open_hook(path, *args, **kwargs):
    handle = real_open(path, *args, **kwargs)
    if os.fspath(path) == source and kwargs.get("encoding") == "utf-8":
        if hook == "rotate-after-open":
            replacement = Path(source + ".replacement")
            old = os.fstat(handle.fileno())
            replacement.write_text("bobby: hello world\n", encoding="utf-8")
            os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
            replacement.replace(source)
            # Unlinking the opened inode can itself update its ctime. Capture
            # the exact opened-FD snapshot after replacement, before read.
            stat = os.fstat(handle.fileno())
            snapshot = [stat.st_dev, stat.st_ino, stat.st_size,
                        stat.st_mtime_ns, stat.st_ctime_ns]
            Path("opened-snapshot.json").write_text(json.dumps(snapshot))
        return Reader(handle)
    return handle

builtins.open = open_hook
exec(compile(code, "<real viewer_chat_monitor producer>", "exec"),
     {"__name__": "__main__"})
'''


def snapshot(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


class SorenOverlayCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="soren cache 日本語 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "lib").mkdir()
        shutil.copyfile(ROOT / "lib/viewer_chat_cache.py",
                        self.root / "lib/viewer_chat_cache.py")
        shutil.copyfile(ROOT / "viewer_chat_monitor.sh",
                        self.root / "viewer_chat_monitor.sh")
        (self.root / "viewer_chat_monitor.sh").chmod(0o755)
        # Do not source any worker, service, audio or OBS initialization.
        (self.root / "eloop_lib.sh").write_text(":\n", encoding="utf-8")
        entry_dir = self.root / "test-bin"
        entry_dir.mkdir()
        entry = entry_dir / "python3"
        entry.write_text("#!" + sys.executable + "\n" + PRODUCER_ENTRY,
                         encoding="utf-8")
        entry.chmod(0o755)
        self.source = self.root / "入力 history.log"
        self.monitor = self.root / "出力 cache" / "monitor.json"
        self.env = {
            "PATH": str(entry_dir) + os.pathsep + os.environ.get("PATH", os.defpath),
            "HOME": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "VIEWER_CHAT_MONITOR_SOURCE": str(self.source),
            "VIEWER_CHAT_MONITOR_FILE": str(self.monitor),
            "VIEWER_CHAT_MONITOR_LOOKBACK": "200",
        }

    def calls(self):
        path = self.root / "producer-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def payload(self):
        return json.loads(self.monitor.read_text(encoding="utf-8"))

    def write_source(self, text="alice: hello world\n"):
        self.source.write_text(text, encoding="utf-8")

    def refresh(self, hook="", lookback="200"):
        result = subprocess.run(
            [sys.executable, "-c", REFRESH], cwd=self.root,
            env=dict(self.env, CACHE_TEST_HOOK=hook,
                     VIEWER_CHAT_MONITOR_LOOKBACK=str(lookback)),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(result.stdout.strip(), ("True", "False"))
        return result.stdout.strip() == "True"

    def test_absent_first_unchanged_append_and_truncation(self):
        self.assertFalse(self.refresh())
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.monitor.exists())

        self.write_source()
        expected = snapshot(self.source)
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["source_snapshot"], expected)
        self.assertEqual(self.payload()["source"], str(self.source))
        self.assertEqual(self.payload()["lookback"], 200)
        self.assertEqual(self.payload()["latest"], "alice: hello world")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 1)
        with self.source.open("a", encoding="utf-8") as stream:
            stream.write("bob: latest update\n")
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["recent"], ["alice: hello world", "bob: latest update"])
        self.assertEqual(self.payload()["count"], 2)

        self.write_source("")
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "")
        self.assertEqual(self.payload()["count"], 0)
        self.assertEqual(len(self.calls()), 3)
        self.assertTrue(all(call[3] == "json" for call in self.calls()))
        self.assertEqual(list(self.monitor.parent.glob(".viewer_chat_monitor-*")), [])

    def test_cache_output_mtime_is_never_the_input_watermark(self):
        self.write_source()
        self.assertTrue(self.refresh())
        os.utime(self.monitor, ns=(1, 1))
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 1)

        self.write_source("bob: newer content\n")
        old = 1_000_000_000
        os.utime(self.source, ns=(old, old))
        future = time.time_ns() + 3_600_000_000_000
        os.utime(self.monitor, ns=(future, future))
        self.assertLess(self.source.stat().st_mtime_ns, self.monitor.stat().st_mtime_ns)
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "bob: newer content")
        self.assertEqual(len(self.calls()), 2)

    def test_effective_lookback_changes_refresh_once(self):
        self.write_source("".join(f"viewer: message {n}\n" for n in range(30)))
        self.assertTrue(self.refresh(lookback=1))
        self.assertEqual(self.payload()["lookback"], 20)
        self.assertEqual(self.payload()["count"], 20)
        self.assertFalse(self.refresh(lookback=20))
        self.assertTrue(self.refresh(lookback=21))
        self.assertEqual(self.payload()["count"], 21)
        self.assertTrue(self.refresh(lookback="invalid"))
        self.assertEqual(self.payload()["lookback"], 200)
        self.assertEqual(self.payload()["count"], 30)
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 3)

    def test_same_mtime_same_size_inode_rotation_is_detected(self):
        self.write_source()
        self.assertTrue(self.refresh())
        old = self.source.stat()
        replacement = self.root / "replacement"
        replacement.write_text("bobby: hello world\n", encoding="utf-8")
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement.replace(self.source)
        self.assertNotEqual(self.source.stat().st_ino, old.st_ino)
        self.assertEqual(self.source.stat().st_size, old.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns, old.st_mtime_ns)
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "bobby: hello world")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 2)

    def test_same_mtime_same_inode_overwrite_is_detected_by_ctime(self):
        self.write_source()
        self.assertTrue(self.refresh())
        old = self.source.stat()
        self.write_source("bobby: hello world\n")
        os.utime(self.source, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.source.stat().st_ino, old.st_ino)
        self.assertEqual(self.source.stat().st_size, old.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns, old.st_mtime_ns)
        self.assertNotEqual(self.source.stat().st_ctime_ns, old.st_ctime_ns)
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "bobby: hello world")

    def test_append_after_read_before_publish_is_seen_next_tick(self):
        self.write_source()
        os.utime(self.source, ns=(1_000_000_000, 1_000_000_000))
        before_read = snapshot(self.source)
        process = subprocess.Popen(
            [sys.executable, "-c", REFRESH], cwd=self.root,
            env=dict(self.env, CACHE_TEST_HOOK="after-read"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "read-done").exists():
                self.assertIsNone(process.poll(), "producer stopped before read hook")
                self.assertLess(time.monotonic(), deadline, "producer did not reach read hook")
                time.sleep(0.005)
            with self.source.open("a", encoding="utf-8") as stream:
                stream.write("bob: appended during publication\n")
            os.utime(self.source, ns=(2_000_000_000, 2_000_000_000))
            (self.root / "continue-read").write_text("go", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "True")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(self.payload()["source_snapshot"], before_read)
        self.assertEqual(self.payload()["latest"], "alice: hello world")
        self.assertLess(self.source.stat().st_mtime_ns, self.monitor.stat().st_mtime_ns)
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "bob: appended during publication")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 2)

    def test_snapshot_belongs_to_opened_fd_when_path_rotates(self):
        self.write_source()
        self.assertTrue(self.refresh(hook="rotate-after-open"))
        opened = json.loads((self.root / "opened-snapshot.json").read_text())
        self.assertEqual(self.payload()["source_snapshot"], opened)
        self.assertNotEqual(opened[1], self.source.stat().st_ino)
        self.assertEqual(self.payload()["latest"], "alice: hello world")
        self.assertTrue(self.refresh())
        self.assertEqual(self.payload()["latest"], "bobby: hello world")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 2)

    def test_read_failure_preserves_cache_and_recovers_without_source_write(self):
        self.write_source()
        self.assertTrue(self.refresh())
        prior_bytes, prior_stat = self.monitor.read_bytes(), self.monitor.stat()
        self.write_source("bob: recovered without another write\n")
        source_stat = snapshot(self.source)
        failed = subprocess.run(
            ["bash", "viewer_chat_monitor.sh", "json"], cwd=self.root,
            env=dict(self.env, CACHE_TEST_HOOK="read-error"),
            capture_output=True, text=True, timeout=10)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.refresh(hook="read-error"))
        self.assertEqual(self.monitor.read_bytes(), prior_bytes)
        self.assertEqual(self.monitor.stat().st_ino, prior_stat.st_ino)
        self.assertEqual(self.monitor.stat().st_mtime_ns, prior_stat.st_mtime_ns)
        self.assertTrue(self.refresh())
        self.assertEqual(snapshot(self.source), source_stat)
        self.assertEqual(self.payload()["latest"], "bob: recovered without another write")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 4)

    def test_failed_first_read_and_nonzero_producer_remain_retryable(self):
        self.write_source()
        source_stat = snapshot(self.source)
        for hook in ("read-error", "nonzero", "nonzero"):
            with self.subTest(hook=hook):
                self.assertFalse(self.refresh(hook=hook))
                self.assertFalse(self.monitor.exists())
        self.assertTrue(self.refresh())
        self.assertEqual(snapshot(self.source), source_stat)
        self.assertEqual(self.payload()["latest"], "alice: hello world")
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 4)

    def test_corrupt_legacy_and_wrong_source_caches_are_repaired(self):
        self.write_source()
        self.assertTrue(self.refresh())
        valid = self.payload()
        legacy = {key: value for key, value in valid.items() if key != "source_snapshot"}
        variants = ["{corrupt", "[]", json.dumps(legacy),
                    json.dumps(dict(valid, source="different-history.log")),
                    json.dumps(dict(valid, latest=None)),
                    json.dumps(dict(valid, recent=[None]))]
        for index, content in enumerate(variants, 2):
            with self.subTest(content=content):
                self.monitor.write_text(content, encoding="utf-8")
                self.assertTrue(self.refresh())
                self.assertEqual(self.payload()["source_snapshot"], snapshot(self.source))
                self.assertEqual(self.payload()["latest"], "alice: hello world")
                self.assertFalse(self.refresh())
                self.assertEqual(len(self.calls()), index)

    def test_source_removal_clears_existing_cache_once(self):
        self.write_source()
        self.assertTrue(self.refresh())
        self.source.unlink()
        self.assertTrue(self.refresh())
        self.assertIsNone(self.payload()["source_snapshot"])
        self.assertEqual(self.payload()["latest"], "")
        self.assertEqual(self.payload()["recent"], [])
        self.assertEqual(self.payload()["count"], 0)
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.calls()), 2)


if __name__ == "__main__":
    unittest.main()
