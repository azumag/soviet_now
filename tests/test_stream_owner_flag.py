"""DOCICH_STREAM_OWNER: exactly one side starts the encoder (ADR 0007)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
START_ALL = REPO_ROOT / "start_all.sh"
DIRECT_STREAM = REPO_ROOT / "lib" / "direct_stream.py"
UNSET = object()


def worker_config(owner=UNSET, backend="ffmpeg"):
    env = os.environ.copy()
    env["SOREN_ENV_FILE"] = str(REPO_ROOT / "tests" / "missing.env")
    env["SOREN_STREAM_BACKEND"] = backend
    env.pop("DOCICH_STREAM_OWNER", None)
    if owner is not UNSET:
        env["DOCICH_STREAM_OWNER"] = owner
    return subprocess.run(["bash", str(START_ALL), "--print-worker-config"], cwd=REPO_ROOT, env=env, text=True,
                          capture_output=True, check=False)


class StartAllOwnerTest(unittest.TestCase):
    def workers(self, owner=UNSET, backend="ffmpeg"):
        result = worker_config(owner, backend)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["workers"]

    def test_the_legacy_runner_stays_the_default_owner(self):
        baseline = self.workers()
        self.assertIn("direct_stream", baseline)
        for owner in ("soren", ""):
            with self.subTest(owner=owner):
                self.assertEqual(self.workers(owner), baseline)

    def test_docich_ownership_removes_only_the_encoder_worker(self):
        baseline = self.workers()
        docich = self.workers("docich")
        self.assertNotIn("direct_stream", docich)
        self.assertEqual([w for w in baseline if w != "direct_stream"], docich, "order and the rest must not change")
        self.assertIn("stream_noon_audit", docich)

    def test_an_invalid_owner_fails_closed_for_the_ffmpeg_backend(self):
        for bad in ("Docich", " docich", "soren ", "both", "1"):
            with self.subTest(owner=bad):
                result = worker_config(bad)
                self.assertEqual(result.returncode, 2)
                self.assertIn("DOCICH_STREAM_OWNER must be soren or docich", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_the_obs_backend_never_starts_the_encoder_regardless_of_the_flag(self):
        for owner in (UNSET, "soren", "docich"):
            with self.subTest(owner=owner):
                workers = self.workers(owner, backend="obs")
                self.assertNotIn("direct_stream", workers)
                self.assertIn("obs_capture_watchdog", workers)


class DirectStreamRunOwnerTest(unittest.TestCase):
    def run_direct(self, owner=UNSET):
        with tempfile.TemporaryDirectory(prefix="owner-flag-") as tmp:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "SOREN_STREAM_BACKEND": "ffmpeg",
                   "SOREN_DIRECT_STREAM_STATE_DIR": str(Path(tmp) / "state"),
                   "SOREN_DIRECT_STREAM_LOG_FILE": str(Path(tmp) / "logs" / "s.log"),
                   "SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://127.0.0.1:1/none/live", "DISPLAY": ":99",
                   "XDG_RUNTIME_DIR": "/run/user/1"}
            if owner is not UNSET:
                env["DOCICH_STREAM_OWNER"] = owner
            proc = subprocess.run([sys.executable, str(DIRECT_STREAM), "run"], capture_output=True, text=True,
                                  timeout=30, check=False, env=env, cwd=str(REPO_ROOT))
            created = sorted(p.name for p in Path(tmp).iterdir())
            return proc, created

    def test_docich_ownership_makes_run_a_no_op(self):
        proc, created = self.run_direct("docich")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("docich-stream.service owns the encoder", proc.stderr)
        self.assertEqual(created, [], "nothing may be created or started")

    def test_the_default_owner_still_takes_the_normal_start_path(self):
        for owner in (UNSET, "soren", ""):
            with self.subTest(owner=owner):
                proc, created = self.run_direct(owner)
                self.assertNotIn("docich-stream.service owns", proc.stderr)
                # The test host cannot stream (no relay on port 1 / not Linux),
                # so the normal path stops at host validation without side effects.
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(created, [])

    def test_an_invalid_owner_is_a_failure_not_a_silent_skip(self):
        for bad in ("Docich", " docich", "both"):
            with self.subTest(owner=bad):
                proc, _created = self.run_direct(bad)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("DOCICH_STREAM_OWNER must be soren or docich", proc.stderr)


if __name__ == "__main__":
    unittest.main()
