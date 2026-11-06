"""Soren keeps the safety guard even when docich deploys a bad module."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "rtmp://127.0.0.1:1935/soren/live"
SCRIPT = """
import importlib.util,json,sys
from pathlib import Path
path=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('direct_stream_guard_fixture',path)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
url=sys.argv[1]
try:
    out=module.load_config({'SOREN_STREAM_BACKEND':'ffmpeg',
        'SOREN_DIRECT_STREAM_LOCAL_URL':url}).local_rtmp_url
    print(json.dumps({'url':out}))
except module.ConfigError as err:
    print(json.dumps({'error':str(err),'errorClass':type(err).__name__}))
"""


class StreamOutputGuardDelegateTest(unittest.TestCase):
    def probe(self, root, url=DEFAULT_URL):
        proc = subprocess.run(
            [sys.executable, "-c", SCRIPT, url], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root),
                 "DOCICH_STREAM_GUARD_MARKER": str(root / "marker")},
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_standalone_wrong_version_malformed_and_throwing_are_safe(self):
        with tempfile.TemporaryDirectory(prefix="stream-output-guard-") as tmp:
            root = Path(tmp)
            module = root / "src/docich/stream_output_guard.py"
            self.assertEqual(self.probe(root), {"url": DEFAULT_URL})
            module.parent.mkdir(parents=True)
            for body in (
                "this is not python!",
                "LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION=2\n"
                "def validate_loopback_rtmp_url(raw): return raw\n",
                "LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION=1\n"
                "def validate_loopback_rtmp_url(raw): raise RuntimeError('invalid')\n",
                "LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION=1\n"
                "def validate_loopback_rtmp_url(raw): return 'rtmp://external.example/live'\n",
            ):
                module.write_text(body, encoding="utf-8")
                self.assertEqual(self.probe(root), {"url": DEFAULT_URL})

    def test_matching_docich_is_used_but_cannot_override_rejected_urls(self):
        with tempfile.TemporaryDirectory(prefix="stream-output-guard-") as tmp:
            root = Path(tmp)
            module = root / "src/docich/stream_output_guard.py"
            module.parent.mkdir(parents=True)
            module.write_text(
                "import os\nfrom pathlib import Path\n"
                "LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION=1\n"
                "def validate_loopback_rtmp_url(raw):\n"
                "    Path(os.environ['DOCICH_STREAM_GUARD_MARKER']).write_text('delegated')\n"
                "    return raw\n", encoding="utf-8",
            )
            self.assertEqual(self.probe(root), {"url": DEFAULT_URL})
            marker = root / "marker"
            self.assertEqual(marker.read_text(), "delegated")
            marker.unlink()
            for url, expected in (
                ("rtmp://stream.example/live", "must target loopback"),
                ("rtmps://localhost/live", "must use rtmp://"),
                ("rtmp://user:password@localhost/live", "must not contain credentials"),
                ("rtmp://localhost/live?key=secret", "must not include query or fragment"),
                ("rtmp://localhost", "include an application/stream path"),
            ):
                with self.subTest(url=url):
                    result = self.probe(root, url)
                    self.assertEqual(result["errorClass"], "ConfigError")
                    self.assertIn(expected, result["error"])
                    self.assertFalse(marker.exists(),
                                     "untrusted shared validator must not be called for rejected URLs")


if __name__ == "__main__":
    unittest.main()
