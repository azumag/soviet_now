import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class DirectStreamBenchmarkTests(unittest.TestCase):
    def test_print_config_is_non_mutating_and_describes_preliminary_limit(self) -> None:
        env = os.environ.copy()
        env["SOREN_ENV_FILE"] = str(REPO_ROOT / "tests" / "missing.env")
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "benchmark_direct_stream.sh"), "--print-config"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads(result.stdout)
        self.assertEqual(
            config["profiles"],
            ["obs_baseline", "actual2", "native4", "affinity2"],
        )
        self.assertIn("actual2 on exactly 2 CPUs", config["selection"])
        self.assertEqual(config["affinity2_allowed_cpus"], "0,1")
        self.assertFalse(config["memory_limit_applied"])
        self.assertTrue(config["memory_observation_only"])
        self.assertFalse(config["actual_shape_resize"])

    def test_benchmark_collects_obs_baseline_before_stopping_obs(self) -> None:
        source = (REPO_ROOT / "benchmark_direct_stream.sh").read_text(encoding="utf-8")
        self.assertIn("benchmark_obs_baseline", source)
        self.assertLess(source.rindex("benchmark_obs_baseline\n"), source.index('./obs_control.sh stream-stop'))
        self.assertIn("from lib.direct_benchmark import build_comparison", source)
        self.assertIn("summary[\"comparison\"] = build_comparison", source)
        self.assertIn('BENCHMARK_MODE=actual2', source)
        self.assertIn('benchmark_profile actual2', source)
        self.assertIn('pidstat -u 1 "$DURATION"', source)

    def test_live_interruption_requires_explicit_confirmation(self) -> None:
        env = os.environ.copy()
        env["SOREN_ENV_FILE"] = str(REPO_ROOT / "tests" / "missing.env")
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "benchmark_direct_stream.sh"), "--duration", "10"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        # macOS is rejected first in the executable path, but it must never
        # reach any mutating command without the confirmation flag.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-live-interruption", result.stderr)

    def test_obs_restore_waits_for_streaming_state_and_retries_start(self) -> None:
        source = (REPO_ROOT / "benchmark_direct_stream.sh").read_text(encoding="utf-8")
        match = re.search(r"\nwait_for_obs_stream_restore\(\) \{\n.*?\n\}\n", source, re.DOTALL)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").write_text("0\n", encoding="utf-8")
            control = root / "obs_control.sh"
            control.write_text(
                """#!/bin/bash
case "$1" in
stream-status)
  starts=$(cat state)
  if [ "$starts" -ge 2 ]; then echo streaming=on; else echo streaming=off; fi
  ;;
stream-start)
  starts=$(cat state)
  echo $((starts + 1)) > state
  echo stream-start:started
  ;;
esac
""",
                encoding="utf-8",
            )
            control.chmod(0o755)
            runner = root / "run.sh"
            runner.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                "OUTPUT_ROOT=.\nOBS_RESTORE_ATTEMPTS=12\nOBS_RESTORE_POLL_SEC=0\n"
                + match.group(0)
                + "wait_for_obs_stream_restore\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(runner)], cwd=root, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "state").read_text(encoding="utf-8").strip(), "2")
            restore_log = (root / "obs-restore.log").read_text(encoding="utf-8")
            self.assertIn("attempt=6", restore_log)
            self.assertIn("attempt=11", restore_log)


if __name__ == "__main__":
    unittest.main()
