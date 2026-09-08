"""Native writer contract; all output stays in temporary roots, OBS is a stub."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


def prepare_root(root, source=SOURCE):
    (root / "core").mkdir(parents=True)
    for name in ("overlay_notify.sh", "generate_event_overlay.py", "core/config.sh"):
        shutil.copyfile(source / name, root / name)
    obs = root / "obs_control.sh"
    obs.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> obs_calls.txt\n')
    obs.chmod(0o755)
    # Core config migrations are outside the writer under test.
    (root / "tmp/state").mkdir(parents=True)
    (root / "tmp/state/.migrated").touch()


def native_env(root, events=None):
    return dict(os.environ, EXPLORE_MODE="0", EVENT_OVERLAY_KEEP_EVENTS="500",
                EVENT_OVERLAY_EVENTS_FILE=str(events or root / "tmp/state/overlay_events.jsonl"),
                EVENT_OVERLAY_HTML_FILE=str(root / "tmp/state/event_overlay.html"),
                OVERLAY_NOTIFY_OBS_SHOW="0")


def native_command(root, title="native"):
    return ["bash", str(root / "overlay_notify.sh"), "worker", title, "通知本文", "info"]


class TestNativeOverlayLock(unittest.TestCase):
    def test_native_recovers_when_lock_owner_dies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_root(root)
            events = root / "tmp/state/overlay_events.jsonl"
            lock_path = events.with_name(events.name + ".lock")
            owner = subprocess.Popen([
                sys.executable, "-c",
                "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); time.sleep(30)",
                str(lock_path),
            ], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(owner.stdout.readline().strip(), "locked")
                inode = lock_path.stat().st_ino
            finally:
                owner.kill()
                owner.communicate(timeout=5)
            result = subprocess.run(native_command(root), env=native_env(root), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(len(events.read_text().splitlines()), 1)
            self.assertEqual(lock_path.stat().st_ino, inode)

    def test_native_lock_timeout_does_not_publish_or_touch_obs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_root(root)
            events = root / "tmp/state/overlay_events.jsonl"
            with events.with_name(events.name + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run(native_command(root), env=native_env(root), capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"another overlay edit in progress", result.stderr)
            self.assertFalse(events.exists())
            self.assertFalse((root / "obs_calls.txt").exists())

    def test_native_preserves_trimming_and_explore_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_root(root)
            env = native_env(root)
            env["EVENT_OVERLAY_KEEP_EVENTS"] = "2"
            for title in ("one", "two", "three"):
                subprocess.run(native_command(root, title), env=env, check=True, capture_output=True, timeout=10)
            events = root / "tmp/state/overlay_events.jsonl"
            self.assertEqual([json.loads(x)["title"] for x in events.read_text().splitlines()], ["two", "three"])
            previous = events.read_bytes()
            env["EXPLORE_MODE"] = "1"
            subprocess.run(native_command(root, "ignored"), env=env, check=True, capture_output=True, timeout=10)
            self.assertEqual(events.read_bytes(), previous)
            self.assertEqual(len((root / "obs_calls.txt").read_text().splitlines()), 3)

    def test_native_waits_for_shared_lock_even_when_mtime_is_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_root(root)
            events = root / "custom/events.jsonl"
            events.parent.mkdir()
            lock_path = events.with_name(events.name + ".lock")
            with lock_path.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                os.utime(lock_path, (1, 1))
                child = subprocess.Popen(native_command(root), env=native_env(root, events),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.communicate(timeout=1)
                    self.assertFalse(events.exists())
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    _, err = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, err.decode())
            self.assertEqual([json.loads(x)["title"] for x in events.read_text().splitlines()], ["native"])
            self.assertTrue(lock_path.is_file())
            self.assertGreater((root / "tmp/state/event_overlay.html").stat().st_size, 0)

    def test_concurrent_native_writers_preserve_every_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_root(root)
            children = [subprocess.Popen(native_command(root, f"native-{i}"), env=native_env(root),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(24)]
            try:
                for child in children:
                    _, err = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, err.decode())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
            rows = [json.loads(x) for x in (root / "tmp/state/overlay_events.jsonl").read_text().splitlines()]
            self.assertEqual({x["title"] for x in rows}, {f"native-{i}" for i in range(24)})
            self.assertEqual(len(rows), 24)
            self.assertTrue(all(x["body"] == "通知本文" and x["level"] == "info" for x in rows))


if __name__ == "__main__":
    unittest.main()
