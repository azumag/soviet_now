"""Soren keeps its own config loader; docich is adopted only when identical."""
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
from dataclasses import asdict
from pathlib import Path
f=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('direct_stream_config_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
env=json.loads(sys.argv[1])
out={'loaded':m._docich_stream_config is not None}
try:
    config=m.load_config(env)
    plain=m._config_as_plain_dict(config)
    out['config']=plain
    out['types']=[isinstance(getattr(config,k),Path) for k in ('closed_captions_socket','state_dir','log_file')]
    out['public_has_url']='local_rtmp_url' in config.public_dict()
except m.ConfigError as err:
    out.update(error=str(err), errorClass=type(err).__name__)
print(json.dumps(out))
"""
ENV = {"SOREN_STREAM_BACKEND": "ffmpeg", "DOCICH_CC_ENABLED": "0"}


def module(version=1, body="return json.loads(os.environ['FIXTURE_CONFIG'])"):
    return (
        "import json,os\nfrom pathlib import Path\n"
        f"STREAM_CONFIG_CONTRACT_VERSION={version}\n"
        "def load_stream_config(env, *, repo_root, runtime_uid):\n"
        "    Path(os.environ['FIXTURE_MARKER']).write_text('config')\n"
        f"    {body}\n"
    )


class ConfigDelegateTest(unittest.TestCase):
    def probe(self, root, env=None, **extra):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, json.dumps(ENV if env is None else env)], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root), "FIXTURE_MARKER": str(root / "marker"), **extra},
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def write(self, root, body):
        target = root / "src/docich/stream_config.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_standalone_and_unsafe_modules_keep_the_local_config(self):
        with tempfile.TemporaryDirectory(prefix="stream-config-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.assertFalse(base["loaded"])
            self.assertEqual(base["types"], [True, True, True])
            self.assertFalse(base["public_has_url"])
            relaxed = json.dumps({**base["config"], "fps": 60, "local_rtmp_url": "rtmp://ext.example/live"})
            for index, body in enumerate((
                "this is not python!",
                module(version=2),
                module(body="raise ValueError('boom')"),
                module(body="return json.loads(os.environ['FIXTURE_RELAXED'])"),  # different values
                module(body="return [1]"),                                         # wrong type
            )):
                self.write(root, body)
                with self.subTest(case=index):
                    got = self.probe(root, FIXTURE_RELAXED=relaxed)
                    self.assertEqual(got["config"], base["config"])
                    self.assertEqual(got["config"]["local_rtmp_url"], "rtmp://127.0.0.1:1935/soren/live")

    def test_identical_shared_config_is_adopted_and_rejected_values_never_delegated(self):
        with tempfile.TemporaryDirectory(prefix="stream-config-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.write(root, module())
            got = self.probe(root, FIXTURE_CONFIG=json.dumps(base["config"]))
            self.assertTrue(got["loaded"])
            self.assertEqual((root / "marker").read_text(), "config")
            self.assertEqual(got["config"], base["config"])
            self.assertEqual(got["types"], [True, True, True])
            (root / "marker").unlink()
            for bad, text in (
                ({"SOREN_DIRECT_STREAM_FPS": "0"}, "between 1 and 60"),
                ({"SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://stream.example/live"}, "must target loopback"),
                ({"SOREN_DIRECT_STREAM_SIZE": "1281x720"}, "must be even"),
            ):
                with self.subTest(bad=bad):
                    result = self.probe(root, env={**ENV, **bad}, FIXTURE_CONFIG=json.dumps(base["config"]))
                    self.assertEqual(result["errorClass"], "ConfigError")
                    self.assertIn(text, result["error"])
                    self.assertFalse((root / "marker").exists())

    def test_legacy_obs_value_stays_with_the_local_loader(self):
        with tempfile.TemporaryDirectory(prefix="stream-config-") as tmp:
            root = Path(tmp)
            self.write(root, module(body="raise ValueError('SOREN_STREAM_BACKEND must be ffmpeg')"))
            got = self.probe(root, env={"SOREN_STREAM_BACKEND": "obs"})
            self.assertEqual(got["config"]["backend"], "obs")


if __name__ == "__main__":
    unittest.main()
