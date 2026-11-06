"""Production remains off; when enabled the fixed lock fences all state dirs."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import importlib.util,json,sys
from pathlib import Path
f=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('direct_stream_canonical_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
m._CANONICAL_STREAM_ENCODER_LOCK_PATH=Path(sys.argv[1])
m.validate_runtime=lambda *a,**kw: False
m.build_ffmpeg_command=lambda *a,**kw: ['fake-ffmpeg']
m._run_ffmpeg_once=lambda *a,**kw: type('Outcome',(),{'exit_code':0})()
cfg=m.load_config({'SOREN_STREAM_BACKEND':'ffmpeg',
   'SOREN_DIRECT_STREAM_STATE_DIR':sys.argv[2]})
try:
    m._run_ffmpeg(cfg,mode='record',output_path=None,duration_sec=None)
    print(json.dumps({'result':'started'}))
except (m.AlreadyRunningError,m.RuntimeCheckError,m.ConfigError) as exc:
    print(json.dumps({'result':type(exc).__name__,'message':str(exc)}))
"""


class CanonicalStreamLockDelegateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="soren-global-encoder-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.global_dir = self.root / "canonical"
        self.global_dir.mkdir(mode=0o700)
        self.path = self.global_dir / "encoder.lock"
        # The production docich opener creates 0600; fixture open('a+')
        # would otherwise create an insecure 0644 file and fail closed.
        self.path.touch(mode=0o600)
        self.path.chmod(0o600)
        self.docich = self.root / "docich"
        self.mod = self.docich / "src/docich/stream_instance_lock.py"
        self.mod.parent.mkdir(parents=True)

    def install_module(self, *, version=1, canonical=True, kind="correct"):
        code = [
            "import os",
            "import fcntl",
            "from pathlib import Path",
            f"CANONICAL_STREAM_ENCODER_LOCK_CONTRACT_VERSION={version}",
            "CANONICAL_STREAM_ENCODER_LOCK_PATH="
            + repr("/home/ubuntu/docich/run-soren-live/locks/direct_stream_encoder.lock"
                   if canonical else "/tmp/wrong.lock"),
            "def open_canonical_stream_encoder_lock():",
            "    p=Path(os.environ['CANONICAL_LOCK_TEST_FILE'])",
        ]
        if kind == "throw":
            code += ["    raise RuntimeError('broken')"]
        else:
            code += [
                "    s=p.open('a+')",
                "    fcntl.flock(s.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)"
                if kind == "correct" else "    pass",
                "    return s",
            ]
        self.mod.write_text("\n".join(code) + "\n", encoding="utf-8")

    def probe(self, state_dir, *, mode="1", root=None):
        r = subprocess.run(
            [sys.executable, "-c", PROBE, str(self.path), str(state_dir)],
            cwd=ROOT, env={
                **os.environ,
                "DOCICH_ROOT": str(root or self.docich),
                "DOCICH_STREAM_CANONICAL_LOCK_ENABLED": mode,
                "CANONICAL_LOCK_TEST_FILE": str(self.path),
            }, capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_disabled_remains_standalone(self):
        self.assertEqual(self.probe(self.root / "s1", mode="0")["result"], "started")

    def test_invalid_boolean_and_missing_module_are_fail_closed(self):
        self.assertEqual(self.probe(self.root / "s1", mode="true")["result"], "ConfigError")
        self.assertEqual(self.probe(self.root / "s1")["result"], "RuntimeCheckError")

    def test_old_mismatched_and_throwing_docich_module_are_fail_closed(self):
        for options in (
            {"version": 2}, {"canonical": False}, {"kind": "throw"},
        ):
            self.install_module(**options)
            with self.subTest(options=options):
                self.assertEqual(self.probe(self.root / "state1")["result"],
                                 "RuntimeCheckError")

    def test_global_contention_across_different_state_dirs(self):
        self.install_module()
        with self.path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.probe(self.root / "soviet/state")["result"],
                             "AlreadyRunningError")
            self.assertEqual(self.probe(self.root / "docich/state")["result"],
                             "AlreadyRunningError")
        self.assertEqual(self.probe(self.root / "docich/state")["result"], "started")

    def test_permissive_shared_module_still_requires_real_global_flock(self):
        self.install_module(kind="no-op")
        with self.path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.probe(self.root / "different-state")["result"],
                             "AlreadyRunningError")
        self.assertEqual(self.probe(self.root / "different-state")["result"],
                         "started")

    def test_shared_opener_cannot_substitute_different_inode(self):
        self.install_module(kind="no-op")
        replacement = self.root / "another.lock"
        replacement.touch()
        with self.path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = subprocess.run(
                [sys.executable, "-c", PROBE, str(self.path), str(self.root / "s2")],
                cwd=ROOT, env={
                    **os.environ, "DOCICH_ROOT": str(self.docich),
                    "DOCICH_STREAM_CANONICAL_LOCK_ENABLED": "1",
                    "CANONICAL_LOCK_TEST_FILE": str(replacement),
                }, capture_output=True, text=True, timeout=12, check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["result"], "RuntimeCheckError")

    def test_existing_local_lock_is_still_required(self):
        self.install_module()
        state = self.root / "local-state"
        state.mkdir(mode=0o700)
        with (state / "direct_stream.lock").open("a+") as local:
            fcntl.flock(local.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Even with a free global lock, the old owner must win.
            self.assertEqual(self.probe(state)["result"], "AlreadyRunningError")


if __name__ == "__main__":
    unittest.main()
