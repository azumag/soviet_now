import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class StrictProjectionGuardTest(unittest.TestCase):
    def _run(self, work: pathlib.Path, extra_env=None):
        source = work / "candidate.py"
        target = work / "strategy.py"
        source.write_text("NEW\n", encoding="utf-8")
        target.write_text("OLD\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "WORKDIR": str(work),
                "SOURCE": str(source),
                "TARGET": str(target),
                "SOREN_PROJECTION_ROOT": str(work),
                "SOREN_LINEAGE_REF_REPO": str(work / "missing-lineage"),
                "TMP_STATE_DIR": str(work / "state"),
            }
        )
        if extra_env:
            env.update(extra_env)
        script = f'''\
set -u
cd "$WORKDIR"
source "{ROOT / 'core/strategy_runtime.sh'}"
source "{ROOT / 'core/strategy_runtime_strict.sh'}"
rc=0
strategy_runtime_atomic_apply "$SOURCE" "$TARGET" || rc=$?
printf 'rc=%s\\n' "$rc"
printf 'policy=%s\\n' "$(_strategy_runtime_live_apply_policy)"
printf 'target=%s' "$(cat "$TARGET")"
'''
        done = subprocess.run(
            ["bash", "-c", script],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return done

    def test_stale_dry_run_env_is_coerced_to_enforce_and_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            done = self._run(pathlib.Path(td), {"IMPROVE_LIVE_APPLY_POLICY": "dry_run"})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("rc=3", done.stdout)
            self.assertIn("policy=enforce_reviewed", done.stdout)
            self.assertIn("target=OLD", done.stdout)

    def test_force_zero_cannot_disable_guard_on_real_projection(self):
        with tempfile.TemporaryDirectory() as td:
            done = self._run(
                pathlib.Path(td),
                {
                    "IMPROVE_LIVE_APPLY_POLICY": "dry_run",
                    "SOREN_PROJECTION_FORCE": "0",
                },
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("rc=3", done.stdout)
            self.assertIn("policy=enforce_reviewed", done.stdout)
            self.assertIn("target=OLD", done.stdout)

    def test_off_without_second_ack_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            done = self._run(pathlib.Path(td), {"IMPROVE_LIVE_APPLY_POLICY": "off"})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("rc=3", done.stdout)
            self.assertIn("policy=enforce_reviewed", done.stdout)
            self.assertIn("target=OLD", done.stdout)

    def test_explicit_two_key_dry_run_escape_hatch_still_exists(self):
        with tempfile.TemporaryDirectory() as td:
            done = self._run(
                pathlib.Path(td),
                {
                    "IMPROVE_LIVE_APPLY_POLICY": "dry_run",
                    "SOREN_UNSAFE_PROJECTION_APPLY_ACK": "1",
                },
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("rc=0", done.stdout)
            self.assertIn("policy=dry_run", done.stdout)
            self.assertIn("target=NEW", done.stdout)


if __name__ == "__main__":
    unittest.main()
