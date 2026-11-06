"""Soren keeps its supervision policy; docich is adopted only when identical."""
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
spec=importlib.util.spec_from_file_location('direct_stream_supervision_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
env=json.loads(sys.argv[1])
out={'loaded':m._docich_supervision_policy is not None}
try:
    out['reconnect']=m.load_reconnect_config(env).public_dict()
    out['watchdog']=m.load_audio_capture_watchdog_config(env).public_dict()
    out['listing']=[m._classify_source_output_listing(l,42) for l in
        ['', 'junk', 'Source Output #1\\n\\t\\tapplication.process.id = \"42\"\\n',
         'Source Output #1\\n\\t\\tapplication.process.id = \"7\"\\n', 'Source Output #1\\n']]
except m.ConfigError as err:
    out.update(error=str(err), errorClass=type(err).__name__)
print(json.dumps(out))
"""
ENV = {"SOREN_DIRECT_STREAM_TWITCH_CHANNEL": "chan_1", "PULSE_SERVER": "unix:/tmp/pulse/native"}


def fixture(version=1, reconnect="return json.loads(os.environ['FIXTURE_RECONNECT'])",
            watchdog="return json.loads(os.environ['FIXTURE_WATCHDOG'])",
            listing="return 'missing'"):
    return (
        "import json,os\nfrom pathlib import Path\n"
        f"STREAM_SUPERVISION_POLICY_CONTRACT_VERSION={version}\n"
        "def load_reconnect_policy(env):\n"
        "    Path(os.environ['FIXTURE_MARKER']).write_text('reconnect')\n"
        f"    {reconnect}\n"
        "def load_audio_watchdog_policy(env, *, uid):\n"
        f"    {watchdog}\n"
        "def classify_source_output_listing(listing, pid):\n"
        "    Path(os.environ['FIXTURE_MARKER'] + '.listing').write_text('listing')\n"
        f"    {listing}\n"
    )


class SupervisionPolicyDelegateTest(unittest.TestCase):
    def probe(self, root, env=None, **fixture_env):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, json.dumps(ENV if env is None else env)], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root), "FIXTURE_MARKER": str(root / "marker"),
                 **{k: v for k, v in fixture_env.items()}},
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def write(self, root, body):
        module = root / "src/docich/stream_supervision_policy.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(body, encoding="utf-8")

    def test_standalone_and_unsafe_modules_fall_back_to_the_local_policy(self):
        with tempfile.TemporaryDirectory(prefix="stream-supervision-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            self.assertFalse(base["loaded"])
            self.assertEqual(base["reconnect"]["twitch_channel"], "chan_1")
            self.assertEqual(base["listing"], ["missing", "unknown", "ok", "missing", "unknown"])
            relaxed = json.dumps({**base["reconnect"], "probe_interval_sec": 1, "enabled": False})
            bodies = (
                "this is not python!",
                fixture(version=2),
                fixture(reconnect="raise RuntimeError('boom')", watchdog="raise RuntimeError('b')",
                        listing="raise RuntimeError('b')"),
                fixture(reconnect="return json.loads(os.environ['RELAXED'])",
                        watchdog="return {'enabled': False}", listing="return 'missing'"),
                fixture(reconnect="return [1]", watchdog="return None", listing="return 5"),
            )
            for body in bodies:
                self.write(root, body)
                with self.subTest(body=body[:70]):
                    got = self.probe(root, RELAXED=relaxed)
                    self.assertEqual({k: got[k] for k in ("reconnect", "watchdog", "listing")},
                                     {k: base[k] for k in ("reconnect", "watchdog", "listing")})

    def test_identical_shared_policy_is_adopted_and_invalid_input_is_never_delegated(self):
        with tempfile.TemporaryDirectory(prefix="stream-supervision-") as tmp:
            root = Path(tmp)
            base = self.probe(root)
            correct = (
                "if not listing.strip(): return 'missing'\n"
                "    if 'Source Output #' not in listing: return 'unknown'\n"
                "    if 'process.id = \"%d\"' % pid in listing: return 'ok'\n"
                "    return 'missing' if 'process.id' in listing else 'unknown'"
            )
            self.write(root, fixture(listing=correct))
            got = self.probe(root, FIXTURE_RECONNECT=json.dumps(base["reconnect"]),
                             FIXTURE_WATCHDOG=json.dumps(base["watchdog"]))
            self.assertTrue(got["loaded"])
            self.assertEqual((root / "marker").read_text(), "reconnect")
            self.assertEqual(got["reconnect"], base["reconnect"])
            self.assertEqual(got["watchdog"], base["watchdog"])
            self.assertEqual(got["listing"], base["listing"])
            self.assertEqual((root / "marker.listing").read_text(), "listing")
            (root / "marker").unlink()
            for bad, text in (
                ({"SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC": "9"}, "must be between 10 and 600"),
                ({"SOREN_DIRECT_STREAM_TWITCH_CHANNEL": "bad chan"}, "is invalid"),
            ):
                with self.subTest(bad=bad):
                    result = self.probe(root, env={**ENV, **bad},
                                        FIXTURE_RECONNECT=json.dumps(base["reconnect"]),
                                        FIXTURE_WATCHDOG=json.dumps(base["watchdog"]))
                    self.assertEqual(result["errorClass"], "ConfigError")
                    self.assertIn(text, result["error"])
                    self.assertFalse((root / "marker").exists(),
                                     "shared policy must not see rejected configuration")


if __name__ == "__main__":
    unittest.main()
