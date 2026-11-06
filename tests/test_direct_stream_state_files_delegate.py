"""Soren verifies every shared state-file result and falls back to its own."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import importlib.util,json,os,stat,sys
from pathlib import Path
f=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('direct_stream_state_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
work=Path(sys.argv[1])
old=os.umask(0)
try:
    m._ensure_private_directory(work/'x'/'y')
finally:
    os.umask(old)
status=work/'x'/'y'/'status.json'
m._atomic_json(status,{'b':1,'a':'日本語','running':True,'pid':os.getpid(),'output_url':'rtmp://x/y'})
cfg=type('C',(),{'state_dir':work/'x'/'y'})()
shown=m.read_status(cfg)
out={'loaded':m._docich_state_files is not None,
     'text':status.read_text(encoding='utf-8'),
     'modes':[stat.S_IMODE((work/'x').stat().st_mode),stat.S_IMODE((work/'x'/'y').stat().st_mode)],
     'running':shown['running'],'hasUrl':'output_url' in shown,
     'alive':[m._pid_alive(os.getpid()),m._pid_alive(0),m._pid_alive('abc')],
     'leftovers':sorted(p.name for p in status.parent.iterdir())}
print(json.dumps(out))
"""

def module(version=1, pid="return True", ensure="pass", write="pass", read="return {'running': True}"):
    return (
        "import os\nfrom pathlib import Path\n"
        f"STREAM_STATE_FILES_CONTRACT_VERSION={version}\n"
        f"def pid_alive(pid):\n    {pid}\n"
        f"def ensure_private_directory(path):\n    {ensure}\n"
        f"def atomic_write_json(path, payload):\n    {write}\n"
        f"def read_stream_status(state_file, *, alive):\n    {read}\n"
    )


class StateFilesDelegateTest(unittest.TestCase):
    def probe(self, root):
        work = Path(tempfile.mkdtemp(prefix="state-work-", dir=root))
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, str(work)], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root)},
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def write(self, root, body):
        target = root / "src/docich/stream_state_files.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def assert_local_behaviour(self, got):
        self.assertEqual(got["modes"], [0o700, 0o700])
        data = json.loads(got["text"])
        self.assertEqual((data["a"], data["b"], data["output_url"]), ("日本語", 1, "rtmp://x/y"))
        self.assertEqual(got["text"], json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
        self.assertEqual((got["running"], got["hasUrl"]), (True, False))
        self.assertEqual(got["alive"], [True, False, False])
        self.assertEqual(got["leftovers"], ["status.json"])

    def test_standalone_and_unsafe_modules_leave_correct_files(self):
        with tempfile.TemporaryDirectory(prefix="stream-state-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.assertFalse(base["loaded"])
            self.assert_local_behaviour(base)
            bodies = (
                "this is not python!",
                module(version=2),
                module(ensure="raise RuntimeError('x')", write="raise RuntimeError('x')",
                       pid="raise RuntimeError('x')", read="raise RuntimeError('x')"),
                # silently does nothing: no file written, directories untouched
                module(),
                # writes corrupt content
                module(write="path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')"),
                # world-writable directory
                module(ensure="path.mkdir(parents=True, exist_ok=True); os.chmod(path, 0o777)"),
                # wrong answers
                module(pid="return False", read="return {'running': True, 'output_url': 'rtmp://x/y'}"),
                module(pid="return 1", read="return None"),
            )
            for index, body in enumerate(bodies):
                self.write(root, body)
                with self.subTest(case=index):
                    self.assert_local_behaviour(self.probe(root))

    def test_a_correct_shared_module_is_adopted(self):
        with tempfile.TemporaryDirectory(prefix="stream-state-") as tmp:
            root = Path(tmp)
            marker = root / "marker"
            body = module(
                ensure=f"Path({str(marker)!r}).write_text('ensure'); path.mkdir(parents=True, exist_ok=True)"
                       "\n    for p in (path, path.parent): os.chmod(p, 0o700)",
                write=f"import json; Path({str(marker)!r}+'.write').write_text('write'); "
                      "path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\\n', encoding='utf-8')",
            )
            self.write(root, body)
            got = self.probe(root)
            self.assertTrue(got["loaded"])
            self.assertEqual(marker.read_text(), "ensure")
            self.assertEqual(Path(str(marker) + ".write").read_text(), "write")
            self.assertEqual(got["text"], json.dumps(json.loads(got["text"]), ensure_ascii=False, sort_keys=True) + "\n")
            self.assertEqual(got["modes"], [0o700, 0o700])


if __name__ == "__main__":
    unittest.main()
