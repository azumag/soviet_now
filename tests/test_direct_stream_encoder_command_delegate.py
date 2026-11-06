"""Soren keeps its own argv/progress/exit logic; docich is adopted only if identical."""
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
spec=importlib.util.spec_from_file_location('direct_stream_encoder_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
env={'SOREN_STREAM_BACKEND':'ffmpeg','DOCICH_CC_ENABLED':sys.argv[1],
     'SOREN_DIRECT_STREAM_LOCAL_URL':sys.argv[2]}
try:
    cfg=m.load_config(env)
    out={'loaded':m._docich_encoder_command is not None,
         'argv':m.build_ffmpeg_command(cfg,mode='live'),
         'record':m.build_ffmpeg_command(cfg,mode='record',output_path=Path('/tmp/x.mkv'),duration_sec=3),
         'local':m._local_build_ffmpeg_command(cfg,mode='live'),
         'progress':m.parse_progress_lines(['frame=3','speed=1.5x','progress=continue']),
         'exit':[m.classify_ffmpeg_exit(255,stopping=True),m.classify_ffmpeg_exit(2,stopping=False)]}
except m.ConfigError as err:
    out={'error':str(err),'errorClass':type(err).__name__}
print(json.dumps(out))
"""
URL = "rtmp://127.0.0.1:1935/soren/live"


def fixture(version=1, build="return list(__import__('os').environ['FIXTURE_ARGV'].split('\\x1f'))",
            progress="return {'frame': 3, 'speed': 1.5, 'progress': 'continue'}",
            exit_="return (0, 'stopped') if stopping else (return_code, 'completed' if return_code == 0 else 'failed')"):
    return (
        "import os\nfrom pathlib import Path\n"
        f"STREAM_ENCODER_COMMAND_CONTRACT_VERSION={version}\n"
        "def build_direct_stream_ffmpeg_command(**kw):\n"
        "    Path(os.environ['FIXTURE_MARKER']).write_text('build')\n"
        f"    {build}\n"
        "def parse_progress_lines(lines):\n"
        f"    {progress}\n"
        "def classify_ffmpeg_exit(return_code, *, stopping):\n"
        f"    {exit_}\n"
    )


class EncoderCommandDelegateTest(unittest.TestCase):
    def probe(self, root, captions="0", url=URL, argv=""):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, captions, url], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root), "FIXTURE_MARKER": str(root / "marker"),
                 "FIXTURE_ARGV": argv},
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def write(self, root, body):
        module = root / "src/docich/stream_encoder_command.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(body, encoding="utf-8")

    def test_standalone_and_unsafe_modules_fall_back_to_the_local_argv(self):
        with tempfile.TemporaryDirectory(prefix="stream-encoder-cmd-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.assertFalse(base["loaded"])
            self.assertEqual(base["argv"], base["local"])
            self.assertEqual(base["argv"][-1], URL)
            expected = (base["argv"], base["record"], base["progress"], base["exit"])
            bad_bodies = (
                "this is not python!",  # syntax error
                fixture(version=2, build="return ['evil']"),  # wrong contract
                fixture(build="raise RuntimeError('boom')"),  # throws
                fixture(build="return ['ffmpeg', 'rtmp://external.example/live']"),  # different
                fixture(build="return tuple(['x'])"),  # wrong type
                fixture(build="return ['ok', 1]"),  # non-str element
                fixture(progress="return {'frame': 999}", exit_="return (0, 'completed')"),
                fixture(progress="raise ValueError('x')", exit_="raise ValueError('y')"),
            )
            for body in bad_bodies:
                self.write(root, body)
                with self.subTest(body=body[:80]):
                    got = self.probe(root, argv="ffmpeg\x1frtmp://external.example/live")
                    self.assertEqual((got["argv"], got["record"], got["progress"], got["exit"]), expected)
                    self.assertNotIn("external.example", json.dumps(got["argv"]))

    def test_identical_shared_result_is_adopted_and_invalid_config_is_never_delegated(self):
        with tempfile.TemporaryDirectory(prefix="stream-encoder-cmd-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.write(root, fixture())
            got = self.probe(root, argv="\x1f".join(base["argv"]))
            self.assertTrue(got["loaded"])
            self.assertEqual(got["argv"], base["argv"])
            self.assertEqual((root / "marker").read_text(), "build")
            self.assertEqual(got["progress"], base["progress"])
            self.assertEqual(got["exit"], base["exit"])
            (root / "marker").unlink()
            for url, expected in (
                ("rtmp://stream.example/live", "must target loopback"),
                ("rtmp://user:password@localhost/live", "must not contain credentials"),
                ("rtmp://localhost/live?key=secret", "must not include query"),
            ):
                with self.subTest(url=url):
                    result = self.probe(root, url=url, argv="\x1f".join(base["argv"]))
                    self.assertEqual(result["errorClass"], "ConfigError")
                    self.assertIn(expected, result["error"])
                    self.assertFalse((root / "marker").exists(),
                                     "shared builder must not see a rejected configuration")

    def test_captions_enabled_argv_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="stream-encoder-cmd-") as tmp:
            root = Path(tmp)
            on = self.probe(root, captions="1")
            self.assertIn("-vf", on["argv"])
            self.assertIn("-a53cc", on["argv"])
            self.write(root, fixture())
            shared = self.probe(root, captions="1", argv="\x1f".join(on["argv"]))
            self.assertEqual(shared["argv"], on["argv"])
            # A shared builder that drops the caption options is rejected.
            off = self.probe(root, captions="0")
            self.write(root, fixture())
            dropped = self.probe(root, captions="1", argv="\x1f".join(off["argv"]))
            self.assertEqual(dropped["argv"], on["argv"])


if __name__ == "__main__":
    unittest.main()
