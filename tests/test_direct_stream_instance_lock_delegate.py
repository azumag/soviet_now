"""Protect FFmpeg single-instance lock while docich ownership is staged."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "lib/direct_stream.py"
PROBE = """
import importlib.util,json,sys
from pathlib import Path
f=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('stream_instance_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
with Path(sys.argv[1]).open('a+') as handle:
    try:
        m._acquire_encoder_lock_with_local_guard(handle)
    except BlockingIOError:
        print(json.dumps({'status':'busy'}))
    else:
        print(json.dumps({'status':'acquired'}))
"""


class DirectStreamInstanceLockDelegateTest(unittest.TestCase):
    def probe(self, root: Path, lock: Path):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, str(lock)],
            cwd=ROOT, env={**os.environ, "DOCICH_ROOT": str(root),
                           "LOCK_DELEGATION_PROBE": str(root / "marker")},
            capture_output=True, text=True, check=False, timeout=12,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_real_flock_contention_and_release_with_standalone_and_invalid_docich(self):
        with tempfile.TemporaryDirectory(prefix="soren-single-encoder-") as td:
            root = Path(td)
            lock = root / "direct_stream.lock"
            module = root / "src/docich/stream_instance_lock.py"
            module.parent.mkdir(parents=True)
            for candidate in (
                None,
                "syntax error ??",
                "SINGLE_ENCODER_LOCK_CONTRACT_VERSION=2\n"
                "def acquire_nonblocking_encoder_lock(fd): pass\n",
                "SINGLE_ENCODER_LOCK_CONTRACT_VERSION=1\n"
                "def acquire_nonblocking_encoder_lock(fd): pass\n",
                "SINGLE_ENCODER_LOCK_CONTRACT_VERSION=1\n"
                "def acquire_nonblocking_encoder_lock(fd): raise RuntimeError('broken')\n",
                "import fcntl\nSINGLE_ENCODER_LOCK_CONTRACT_VERSION=1\n"
                "def acquire_nonblocking_encoder_lock(fd):\n"
                "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
            ):
                if candidate is None:
                    module.unlink(missing_ok=True)
                else:
                    module.write_text(candidate, encoding="utf-8")
                with self.subTest(candidate=str(candidate)[:65]):
                    with lock.open("a+") as hold:
                        fcntl.flock(hold.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        # The local LOCK_NB fence must reject a second process,
                        # even if the shared function is a permissive no-op.
                        self.assertEqual(self.probe(root, lock), {"status": "busy"})
                    self.assertEqual(self.probe(root, lock), {"status": "acquired"})

    def test_matching_version_is_called_but_must_not_silence_local_flock(self):
        with tempfile.TemporaryDirectory(prefix="soren-single-encoder-") as td:
            root = Path(td)
            lock = root / "direct_stream.lock"
            marker = root / "marker"
            module = root / "src/docich/stream_instance_lock.py"
            module.parent.mkdir(parents=True)
            module.write_text(
                "from pathlib import Path\nimport os\n"
                "SINGLE_ENCODER_LOCK_CONTRACT_VERSION=1\n"
                "def acquire_nonblocking_encoder_lock(fd):\n"
                "    Path(os.environ['LOCK_DELEGATION_PROBE']).write_text('delegated')\n",
                encoding="utf-8",
            )
            self.assertEqual(self.probe(root, lock), {"status": "acquired"})
            self.assertEqual(marker.read_text(), "delegated")
            marker.unlink()
            with lock.open("a+") as hold:
                fcntl.flock(hold.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(self.probe(root, lock), {"status": "busy"})
                self.assertEqual(marker.read_text(), "delegated")

    def test_run_path_retains_one_lock_fd_through_supervisor_lifetime(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('lock_path = config.state_dir / "direct_stream.lock"', source)
        self.assertIn("_acquire_encoder_lock_with_local_guard(lock_stream)", source)
        self.assertIn('raise AlreadyRunningError("direct stream is already running")', source)
        self.assertIn("lock_stream.close()", source)
        self.assertIn("fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)", source)


if __name__ == "__main__":
    unittest.main()
