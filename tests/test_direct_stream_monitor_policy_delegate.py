"""Soren keeps its restart verdicts; a docich verdict is adopted only if identical."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import importlib.util,itertools,json,sys
from pathlib import Path
f=Path('lib/direct_stream.py').resolve()
spec=importlib.util.spec_from_file_location('direct_stream_monitor_fixture',f)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)
twitch=[[list(m._step_twitch_offline_streak(s,l,t)),list(m._local_step_twitch_offline_streak(s,l,t))]
        for s,l,t in itertools.product(range(0,5),(True,False,None),(1,2,3))]
audio=[[list(m._step_audio_capture_loss(st,c,seen,t)),list(m._local_step_audio_capture_loss(st,c,seen,t))]
       for st,c,seen,t in itertools.product(('ok','missing','unreachable','unknown'),range(0,4),(False,True),(1,2,3))]
print(json.dumps({'loaded':m._docich_monitor_policy is not None,'twitch':twitch,'audio':audio}))
"""


def module(version=1, twitch="return (0, False)", audio="return (0, False, False)"):
    return (
        "import os\nfrom pathlib import Path\n"
        f"STREAM_MONITOR_POLICY_CONTRACT_VERSION={version}\n"
        "def step_twitch_offline_streak(offline_streak, live, offline_threshold):\n"
        "    Path(os.environ['FIXTURE_MARKER']).write_text('twitch')\n"
        f"    {twitch}\n"
        "def step_audio_capture_loss(state, consecutive, seen_capture, loss_threshold):\n"
        f"    {audio}\n"
    )


FAITHFUL = module(
    twitch=("if live is None: return (int(offline_streak), False)\n"
            "    if live is True: return (0, False)\n"
            "    return (int(offline_streak) + 1, int(offline_streak) + 1 >= offline_threshold)"),
    audio=("if state == 'ok': return (0, True, False)\n"
           "    if state == 'missing' and seen_capture:\n"
           "        c = int(consecutive) + 1\n"
           "        return (c, True, c >= loss_threshold)\n"
           "    return (0, bool(seen_capture), False)"),
)


class MonitorPolicyDelegateTest(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE], cwd=ROOT,
            env={**os.environ, "DOCICH_ROOT": str(root), "FIXTURE_MARKER": str(root / "marker")},
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def write(self, root, body):
        target = root / "src/docich/stream_monitor_policy.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def assert_matches_local(self, got):
        for wrapped, local in got["twitch"] + got["audio"]:
            self.assertEqual(wrapped, local)

    def test_local_verdicts_keep_the_safety_properties(self):
        with tempfile.TemporaryDirectory(prefix="stream-monitor-") as tmp:
            got = self.probe(Path(tmp))
            self.assertFalse(got["loaded"])
            self.assert_matches_local(got)
            restarts = [w for w, _ in got["twitch"] if w[1]]
            self.assertTrue(restarts)
            self.assertEqual(len(got["audio"]), 4 * 4 * 2 * 3)

    def test_unsafe_modules_can_neither_add_nor_remove_a_restart(self):
        with tempfile.TemporaryDirectory(prefix="stream-monitor-") as tmp:
            root = Path(tmp)
            for index, body in enumerate((
                "this is not python!",
                module(version=2),
                module(twitch="raise RuntimeError('x')", audio="raise RuntimeError('x')"),
                module(twitch="return (99, True)", audio="return (99, True, True)"),   # always restart
                module(twitch="return (0, False)", audio="return (0, False, False)"),  # never restart
                module(twitch="return [1, True]", audio="return None"),                 # wrong types
            )):
                self.write(root, body)
                with self.subTest(case=index):
                    self.assert_matches_local(self.probe(root))

    def test_a_faithful_shared_module_is_adopted(self):
        with tempfile.TemporaryDirectory(prefix="stream-monitor-") as tmp:
            root = Path(tmp)
            self.write(root, FAITHFUL)
            got = self.probe(root)
            self.assertTrue(got["loaded"])
            self.assertEqual((root / "marker").read_text(), "twitch")
            self.assert_matches_local(got)


if __name__ == "__main__":
    unittest.main()
